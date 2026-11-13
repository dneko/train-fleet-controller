"""Remember the reader token in this Mac user's Keychain, outside fleet files."""
import os
import sys

SERVICE = 'org.trainstuff.train-fleet'
ACCOUNT = 'reader-token'
ENVIRONMENT_VARIABLE = 'TRAIN_READER_TOKEN'


class SecretStoreError(RuntimeError):
    pass


def _keychain():
    if sys.platform != 'darwin':
        raise SecretStoreError('Remembering the shared secret requires macOS Keychain.')
    try:
        # Select Keychain explicitly; never fall back to a plaintext backend.
        from keyring.backends.macOS import Keyring
        return Keyring()
    except ImportError:
        raise SecretStoreError(
            'Keychain support is missing. Run .venv/bin/python -m pip install -r requirements.txt.') from None


def load_reader_token():
    # An explicit empty environment value still disables the receiver.
    if ENVIRONMENT_VARIABLE in os.environ:
        return os.environ[ENVIRONMENT_VARIABLE]
    try:
        return _keychain().get_password(SERVICE, ACCOUNT) or ''
    except SecretStoreError:
        raise
    except Exception:
        raise SecretStoreError('Could not read the shared secret from macOS Keychain.') from None


def save_reader_token(token):
    if len(token) < 16:
        raise ValueError('The shared secret must contain at least 16 characters.')
    try:
        _keychain().set_password(SERVICE, ACCOUNT, token)
    except SecretStoreError:
        raise
    except Exception:
        raise SecretStoreError('Could not save the shared secret in macOS Keychain.') from None


def forget_reader_token():
    try:
        keychain = _keychain()
        if keychain.get_password(SERVICE, ACCOUNT) is not None:
            keychain.delete_password(SERVICE, ACCOUNT)
    except SecretStoreError:
        raise
    except Exception:
        raise SecretStoreError('Could not remove the shared secret from macOS Keychain.') from None
