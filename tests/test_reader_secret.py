import os
import unittest
from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import fleet_launcher
from reader_secret import (ACCOUNT, SERVICE, ENVIRONMENT_VARIABLE, SecretStoreError,
                           load_reader_token, save_reader_token, forget_reader_token)


class ReaderSecretTests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)
        backend = patch('reader_secret._keychain')
        self.keychain = backend.start().return_value
        self.addCleanup(backend.stop)

    def test_saved_token_is_reused(self):
        self.keychain.get_password.return_value = 'test-only-shared-secret'
        self.assertEqual(load_reader_token(), 'test-only-shared-secret')
        self.keychain.get_password.assert_called_once_with(SERVICE, ACCOUNT)

    def test_environment_override_and_explicit_disable_skip_keychain(self):
        for value in ('explicit-test-token', ''):
            os.environ[ENVIRONMENT_VARIABLE] = value
            self.assertEqual(load_reader_token(), value)
        self.keychain.get_password.assert_not_called()

    def test_new_install_has_no_saved_token(self):
        self.keychain.get_password.return_value = None
        self.assertEqual(load_reader_token(), '')

    def test_invalid_token_does_not_replace_saved_secret(self):
        for value in ('', 'too-short'):
            with self.assertRaises(ValueError):
                save_reader_token(value)
        self.keychain.set_password.assert_not_called()

    def test_save_and_forget_use_only_the_app_keychain_item(self):
        save_reader_token('test-only-shared-secret')
        self.keychain.set_password.assert_called_once_with(SERVICE, ACCOUNT, 'test-only-shared-secret')
        forget_reader_token()
        self.keychain.delete_password.assert_called_once_with(SERVICE, ACCOUNT)

    def test_forget_when_no_secret_exists_is_harmless(self):
        self.keychain.get_password.return_value = None
        forget_reader_token()
        self.keychain.delete_password.assert_not_called()

    def test_backend_errors_do_not_expose_secret_contents(self):
        for operation, method, args in (
                (load_reader_token, 'get_password', ()),
                (save_reader_token, 'set_password', ('test-only-shared-secret',)),
                (forget_reader_token, 'get_password', ())):
            with self.subTest(operation=operation.__name__):
                self.keychain.reset_mock(side_effect=True)
                getattr(self.keychain, method).side_effect = RuntimeError('sensitive-backend-details')
                with self.assertRaises(SecretStoreError) as caught:
                    operation(*args)
                self.assertNotIn('sensitive-backend-details', str(caught.exception))


class LauncherSecretTests(unittest.TestCase):
    def run_launcher(self, *, demo=False, motor=False, entered=None, forget=False, save_error=None):
        variables, buttons = [], {}
        class Variable:
            def __init__(self, value):
                self.value = value
                variables.append(self)
            def get(self):
                return self.value
            def set(self, value):
                self.value = value
            def trace_add(self, *args):
                pass
        def button(*args, **kwargs):
            buttons[kwargs['text']] = kwargs['command']
            return MagicMock()
        def interact():
            # StringVars: configuration path, startup mode, masked reader token.
            variables[1].set('motor' if motor else 'demo' if demo else 'live')
            if entered is not None:
                variables[2].set(entered)
            if forget:
                buttons['Forget saved secret']()
            buttons['Launch']()
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {}, clear=True))
            root = stack.enter_context(patch.object(fleet_launcher.tk, 'Tk')).return_value
            root.mainloop.side_effect = interact
            stack.enter_context(patch.object(fleet_launcher.tk, 'StringVar', Variable))
            boolean = MagicMock()
            boolean.get.return_value = demo
            stack.enter_context(patch.object(fleet_launcher.tk, 'BooleanVar', return_value=boolean))
            for name in ('Frame', 'Label', 'Entry', 'Radiobutton'):
                stack.enter_context(patch.object(fleet_launcher.ttk, name))
            stack.enter_context(patch.object(fleet_launcher.ttk, 'Button', side_effect=button))
            stack.enter_context(patch.object(fleet_launcher, 'load_config', return_value={}))
            stack.enter_context(patch.object(fleet_launcher, 'load_reader_token', return_value='saved-test-secret'))
            save = stack.enter_context(patch.object(fleet_launcher, 'save_reader_token', side_effect=save_error))
            delete = stack.enter_context(patch.object(fleet_launcher, 'forget_reader_token'))
            warning = stack.enter_context(patch.object(fleet_launcher.messagebox, 'showwarning'))
            result = fleet_launcher.choose_options()
            environment = dict(os.environ)
        return result, save, delete, warning, environment

    def test_live_launch_saves_and_uses_entered_secret(self):
        result, save, _, warning, environment = self.run_launcher(entered='new-test-shared-secret')
        self.assertFalse(result[2])
        save.assert_called_once_with('new-test-shared-secret')
        self.assertEqual(environment[ENVIRONMENT_VARIABLE], 'new-test-shared-secret')
        warning.assert_not_called()

    def test_demo_launch_does_not_save_or_set_reader_token(self):
        result, save, delete, _, environment = self.run_launcher(demo=True, entered='changed-test-secret')
        self.assertTrue(result[2])
        save.assert_not_called()
        delete.assert_not_called()
        self.assertNotIn(ENVIRONMENT_VARIABLE, environment)

    def test_motor_mode_does_not_require_or_save_reader_secret(self):
        result, save, delete, _, environment = self.run_launcher(motor=True, entered='short')
        self.assertEqual(result[2:], (False, True))
        save.assert_not_called()
        delete.assert_not_called()
        self.assertNotIn(ENVIRONMENT_VARIABLE, environment)

    def test_blank_field_disables_readers_without_forgetting(self):
        _, save, delete, _, environment = self.run_launcher(entered='')
        save.assert_not_called()
        delete.assert_not_called()
        self.assertEqual(environment[ENVIRONMENT_VARIABLE], '')

    def test_forget_clears_field_and_does_not_resave_on_launch(self):
        _, save, delete, _, environment = self.run_launcher(forget=True)
        delete.assert_called_once_with()
        save.assert_not_called()
        self.assertEqual(environment[ENVIRONMENT_VARIABLE], '')

    def test_save_failure_warns_and_keeps_live_launch_usable(self):
        result, _, _, warning, environment = self.run_launcher(
            entered='new-test-shared-secret', save_error=SecretStoreError('Keychain unavailable'))
        self.assertFalse(result[2])
        warning.assert_called_once()
        self.assertEqual(environment[ENVIRONMENT_VARIABLE], 'new-test-shared-secret')
