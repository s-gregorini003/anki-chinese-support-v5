# Copyright © 2012 Roland Sieker <ospalh@gmail.com>
# Copyright © 2012 Thomas TEMPÉ <thomas.tempe@alysse.org>
# Copyright © 2017 Pu Anlai <https://github.com/InspectorMustache>
# Copyright © 2019 Oliver Rice <orice@apple.com>
# Copyright © 2017-2021 Joseph Lorimer <joseph@lorimer.me>
# Inspiration: Tymon Warecki
# License: GNU AGPL, version 3 or later; http://www.gnu.org/copyleft/agpl.html

import os
import ssl
from os.path import basename, exists, join
from re import sub
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import json
import tempfile

import requests
from aqt import mw
from gtts import gTTS
from gtts.tts import gTTSError

from .aws import AWS4Signer

requests.packages.urllib3.disable_warnings()


class AudioDownloader:
    def __init__(self, text, source='google|zhCN'):
        self.text = text
        self.service, self.lang = source.split('|')
        ext = 'wav' if self.service == 'qwen_cloud' else 'mp3'
        self.path = self.get_path(ext=ext)
        self.func = {
            'google':     self.get_google,
            'baidu':      self.get_baidu,
            'aws':        self.get_aws,
            'qwen_cloud': self.get_qwen_cloud,
        }.get(self.service)

    def get_path(self, ext='mp3'):
        filename = '{}_{}_{}.{}'.format(
            self.sanitize(self.text), self.service, self.lang, ext
        )
        return join(mw.col.media.dir(), filename)

    def sanitize(self, s):
        return sub(r'[/:*?"<>|]', '', s)

    def download(self):
        if exists(self.path):
            return basename(self.path)

        if not self.func:
            raise NotImplementedError(self.service)

        self.func()

        return basename(self.path)

    def get_google(self):
        tts = gTTS(self.text, lang=self.lang, tld='com')
        try:
            tts.save(self.path)
        except gTTSError as e:
            print('gTTS Error: {}'.format(e))

    def get_baidu(self):
        query = {
            'lan': self.lang,
            'ie': 'UTF-8',
            'text': self.text.encode('utf-8'),
            'spd': 2,
            'source': 'web',
        }

        url = 'https://fanyi.baidu.com/gettts?' + urlencode(query)
        request = Request(url)
        request.add_header('User-Agent', 'Mozilla/5.0')

        # baidu web server seems to behave nondeterministically when the alpn extension is not supplied where it
        # sometimes returns 200 OK but with Content-Length 0
        # when the extension is sent, the audio/mpeg content is returned as expected
        # automatically sending the alpn extension was added in python 3.10, but Anki is currently using 3.9
        context = ssl.create_default_context()
        context.set_alpn_protocols(['http/1.1'])

        with urlopen(request, context=context, timeout=5) as response, open(self.path, 'wb') as audio:
            if response.code != 200:
                raise ValueError('{}: {}'.format(response.code, response.msg))

            bytes_response = response.read()
            audio.write(bytes_response)

    def get_aws(self):
        signer = AWS4Signer(service='polly')
        signer.use_aws_profile('chinese_support_redux')

        url = 'https://polly.%s.amazonaws.com/v1/speech' % (signer.region_name)
        query = {
            'OutputFormat': 'mp3',
            'Text': self.text,
            'VoiceId': self.lang,
        }

        response = requests.post(url, json=query, auth=signer)

        if response.status_code != 200:
            raise ValueError(
                'Polly Request Failed: Error Code {}'.format(
                    response.status_code
                )
            )

        with open(self.path, 'wb') as audio:
            audio.write(response.content)

    def get_qwen_cloud(self):
        """
        Generate speech via the Qwen3-TTS Hugging Face Space using voice cloning.

        Reads from Anki add-on config:
            qwen_ref_audio  : absolute path to the local reference WAV file
            qwen_ref_text   : transcript of the reference audio (Chinese)
            qwen_language   : dropdown value, default "Auto"
            qwen_use_xvector: bool, default True
            qwen_model_size : "0.6B" or "3B", default "0.6B"

        Flow:
            1. Upload ref audio to tmpfiles.org → get public URL
            2. POST to /gradio_api/call/generate_voice_clone → get event_id
            3. GET stream → parse SSE until event: complete
            4. Download result audio → save to self.path
        """
        SPACE_URL = "https://qwen-qwen3-tts.hf.space"

        # ── Read config ────────────────────────────────────────────────────────
        cfg = mw.addonManager.getConfig(__name__) or {}
        ref_audio_path = cfg.get('qwen_ref_audio', '')
        ref_text       = cfg.get('qwen_ref_text', '')
        language       = cfg.get('qwen_language', 'Auto')
        use_xvector    = cfg.get('qwen_use_xvector', True)
        model_size     = cfg.get('qwen_model_size', '0.6B')

        if not ref_audio_path or not exists(ref_audio_path):
            raise FileNotFoundError(
                'Qwen TTS: reference audio not found. '
                'Set "qwen_ref_audio" in the add-on config to an absolute path.'
            )
        if not ref_text:
            raise ValueError(
                'Qwen TTS: "qwen_ref_text" is empty. '
                'Set it in the add-on config to the transcript of your reference audio.'
            )

        session = requests.Session()

        # ── Step 1: Upload ref audio to tmpfiles.org ───────────────────────────
        with open(ref_audio_path, 'rb') as f:
            upload_resp = requests.post(
                'https://tmpfiles.org/api/v1/upload',
                files={'file': (basename(ref_audio_path), f, 'audio/wav')},
                timeout=60,
            )
        upload_resp.raise_for_status()

        viewer_url = upload_resp.json()['data']['url']
        # Convert viewer URL → direct download URL
        # https://tmpfiles.org/XXXX/file.wav → https://tmpfiles.org/dl/XXXX/file.wav
        public_url = viewer_url.replace('tmpfiles.org/', 'tmpfiles.org/dl/')

        ref_audio_obj = {
            'path': public_url,
            'meta': {'_type': 'gradio.FileData'},
        }

        # ── Step 2: Start voice clone job ─────────────────────────────────────
        payload = {
            'data': [
                ref_audio_obj,
                ref_text,
                self.text,      # target text = the word/sentence Anki wants spoken
                language,
                use_xvector,
                model_size,
            ]
        }

        call_resp = session.post(
            SPACE_URL + '/gradio_api/call/generate_voice_clone',
            headers={'Content-Type': 'application/json'},
            data=json.dumps(payload),
            timeout=60,
        )
        call_resp.raise_for_status()

        event_id = call_resp.json().get('event_id')
        if not event_id:
            raise RuntimeError(
                f'Qwen TTS: no event_id in response: {call_resp.text[:200]}'
            )

        # ── Step 3: Stream SSE until event: complete ───────────────────────────
        stream_url = SPACE_URL + f'/gradio_api/call/generate_voice_clone/{event_id}'
        last_obj   = None
        event_type = None

        with session.get(stream_url, stream=True, timeout=300) as stream_resp:
            stream_resp.raise_for_status()

            for raw_line in stream_resp.iter_lines():
                if not raw_line:
                    continue

                decoded = raw_line.decode('utf-8')

                if decoded.startswith('event:'):
                    event_type = decoded[len('event:'):].strip()
                    continue

                if decoded.startswith('data:'):
                    data_str = decoded[len('data:'):].strip()
                    if not data_str or data_str == 'null':
                        continue

                    try:
                        obj = json.loads(data_str)
                        last_obj = obj

                        if event_type == 'complete':
                            break
                        if event_type == 'error':
                            raise RuntimeError(f'Qwen TTS: Space returned error: {obj}')

                    except json.JSONDecodeError:
                        pass   # heartbeat or non-JSON line — ignore

        if last_obj is None:
            raise RuntimeError('Qwen TTS: stream ended with no audio data.')

        # ── Step 4: Download and save audio ───────────────────────────────────
        audio_data = last_obj[0]   # FileData dict
        audio_url  = (
            audio_data.get('url')
            or SPACE_URL + '/gradio_api/file=' + audio_data['path']
        )

        audio_resp = session.get(audio_url, timeout=60)
        audio_resp.raise_for_status()

        with open(self.path, 'wb') as out:
            out.write(audio_resp.content)
            

            