import array
import math
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import wave

from alarm_audio import ALARM_FILE, CHIME_FILE, AlarmSound
from train_alarm import TrainAlarm


class AlarmAudioTests(unittest.TestCase):
    @patch('alarm_audio.sys.platform','darwin')
    @patch('alarm_audio.subprocess.Popen')
    def test_sound_selection_and_mute_stop_active_audio(self, popen):
        fallback = Mock()
        sound = AlarmSound(fallback)
        sound.set_mode('chime')
        sound.play()
        self.assertEqual(popen.call_args.args[0][-1], str(CHIME_FILE))
        popen.return_value.poll.return_value = None
        sound.set_mode('none')
        popen.return_value.terminate.assert_called_once()
        popen.reset_mock()
        sound.play()
        popen.assert_not_called()
        fallback.assert_not_called()
        sound.set_mode('bell')
        sound.play()
        fallback.assert_called_once()

    def test_chime_is_a_valid_distinct_bundled_sound(self):
        self.assertNotEqual(CHIME_FILE.read_bytes(), ALARM_FILE.read_bytes())
        with wave.open(str(CHIME_FILE),'rb') as sound:
            self.assertEqual((sound.getnchannels(),sound.getsampwidth()),(1,2))
            self.assertAlmostEqual(sound.getnframes()/sound.getframerate(),1.5)

    def test_bundled_siren_has_sustained_signal_without_clipping(self):
        with wave.open(str(ALARM_FILE),'rb') as sound:
            self.assertEqual((sound.getnchannels(),sound.getsampwidth()),(1,2))
            self.assertAlmostEqual(sound.getnframes()/sound.getframerate(),2.7)
            samples = array.array('h',sound.readframes(sound.getnframes()))
        peak = max(abs(value) for value in samples)
        rms = math.sqrt(sum(value*value for value in samples)/len(samples))
        self.assertGreater(peak,22000)
        self.assertLess(peak,32767)
        self.assertGreater(rms,15000)

    @patch('alarm_audio.sys.platform','darwin')
    @patch('alarm_audio.subprocess.Popen')
    def test_playback_is_asynchronous_single_instance_and_stoppable(self, popen):
        process = popen.return_value
        process.poll.return_value = None
        fallback = Mock()
        sound = AlarmSound(fallback)
        sound.play()
        sound.play()
        popen.assert_called_once()
        self.assertEqual(popen.call_args.args[0],['/usr/bin/afplay','-v','1',str(ALARM_FILE)])
        process.wait.assert_not_called()
        sound.stop()
        process.terminate.assert_called_once()
        fallback.assert_not_called()

    def test_missing_audio_uses_fallback(self):
        fallback = Mock()
        with patch('alarm_audio.ALARM_FILE',Path('/missing-alarm-file.wav')):
            AlarmSound(fallback).play()
        fallback.assert_called_once()

    def test_silence_clear_and_close_stop_audio_immediately(self):
        bell,stop = Mock(),Mock()
        alarm = TrainAlarm(bell,stop=stop)
        alarm.update({'a':'late'})
        alarm.silence()
        self.assertEqual(stop.call_count,1)
        alarm.update({'a':'late','b':'new'})
        self.assertEqual(bell.call_count,2)
        alarm.update({})
        self.assertEqual(stop.call_count,2)
        alarm.update({'c':'new'})
        alarm.close()
        self.assertEqual(stop.call_count,3)
