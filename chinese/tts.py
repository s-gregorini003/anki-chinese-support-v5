# Copyright © 2012 Roland Sieker <ospalh@gmail.com>
# Copyright © 2012 Thomas TEMPÉ <thomas.tempe@alysse.org>
# Copyright © 2017 Pu Anlai <https://github.com/InspectorMustache>
# Copyright © 2019 Oliver Rice <orice@apple.com>
# Copyright © 2017-2021 Joseph Lorimer <joseph@lorimer.me>
# Inspiration: Tymon Warecki
# License: GNU AGPL, version 3 or later; http://www.gnu.org/copyleft/agpl.html

import ssl
from os.path import basename, exists, join
from re import sub
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import requests
from aqt import mw
from gtts import gTTS
from gtts.tts import gTTSError

from .aws import AWS4Signer

from gradio_client import Client, handle_file

requests.packages.urllib3.disable_warnings()


class AudioDownloader:
    def __init__(self, text, source='google|zh-CN'):
        self.text = text
        self.service, self.lang = source.split('|')
        self.path = self.get_path()
        self.func = {
            'google': self.get_google,
            'baidu': self.get_baidu,
            'aws': self.get_aws,
            'qwen': self.get_qwen_cloud,
        }.get(self.service)

    def get_path(self):
        filename = '{}_{}_{}.mp3'.format(
            self.sanitize(self.text), self.service, self.lang
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
        # Read reference audio config that you'll configure via a UI (see below)
        config = mw.addonManager.getConfig(__name__)
        ref_audio_path = config.get("qwen_ref_audio_path")
        ref_text       = config.get("qwen_ref_text", "")

        if not ref_audio_path or not os.path.exists(ref_audio_path):
            raise RuntimeError("Qwen3-TTS reference audio not configured or file missing.")

        # 1. Construct client targeting the official Space
        client = Client("Qwen/Qwen3-TTS")

        # 2. Call the /generate_voice_clone API with user’s reference audio and text to speak
        result = client.predict(
            ref_audio=handle_file(ref_audio_path),
            ref_text=ref_text,
            target_text=self.text,
            language="Auto",
            use_xvector_only=False,
            model_size="1.7B",
            api_name="/generate_voice_clone",
        )

        # 3. Save returned audio to self.path. The exact type of `result` depends on the Space.
        # The most common patterns:
        if isinstance(result, str):
            # Could be a local filepath or a URL
            if os.path.exists(result):
                shutil.copyfile(result, self.path)
            else:
                # treat it as a URL and download
                import requests
                r = requests.get(result, timeout=30)
                r.raise_for_status()
                with open(self.path, "wb") as f:
                    f.write(r.content)

        elif isinstance(result, dict) and "path" in result:
            shutil.copyfile(result["path"], self.path)
        else:
            # For first run, log result to Anki console so you can inspect its structure
            print("Unexpected Qwen3-TTS result:", repr(result))
            raise RuntimeError("Unexpected result from Qwen3-TTS API.")