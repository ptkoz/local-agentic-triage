import json
import urllib.request

from local_agentic_triage.common.json import strict_json

ENDPOINT='http://127.0.0.1:12434/engines/v1/chat/completions'
MODEL='docker.io/ai/ministral3-mail-8b:16k'
# Total model context window shared by the request and generated response.
LOCAL_MODEL_CONTEXT_TOKEN_LIMIT=16384
# Maximum number of tokens the model may generate in one response.
LOCAL_MODEL_OUTPUT_TOKEN_LIMIT=2048
# Maximum duration of one request to the loopback model endpoint.
LOCAL_MODEL_REQUEST_TIMEOUT_SECONDS=300

class ModelEnvelopeJsonInvalid(ValueError):
    pass


class ModelContentJsonInvalid(ValueError):
    pass


def compact_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): raise ValueError('model redirect forbidden')


def chat_completion(messages,response_format):
    data={'model':MODEL,'temperature':0,'response_format':response_format,'max_tokens':LOCAL_MODEL_OUTPUT_TOKEN_LIMIT,'messages':messages}
    request=urllib.request.Request(ENDPOINT,data=json.dumps(data).encode(),headers={'Content-Type':'application/json'})
    # Disable environment proxies; only this fixed loopback endpoint is permitted.
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
    with opener.open(request,timeout=LOCAL_MODEL_REQUEST_TIMEOUT_SECONDS) as response:
        raw=response.read()
    try:
        envelope=strict_json(raw.decode('utf-8') if isinstance(raw,bytes) else raw)
    except (TypeError,ValueError,UnicodeError):
        raise ModelEnvelopeJsonInvalid from None
    try:
        if (not isinstance(envelope,dict) or not isinstance(envelope['choices'],list)
                or len(envelope['choices'])!=1 or not isinstance(envelope['choices'][0],dict)):
            raise ValueError
        result=envelope['choices'][0]
        finish_reason=result['finish_reason']
        content=result['message']['content']
        if not isinstance(result['message'],dict): raise ValueError
    except (KeyError,IndexError,TypeError,ValueError):
        raise ModelEnvelopeJsonInvalid from None
    if finish_reason!='stop': raise ValueError('model incomplete')
    try:
        return strict_json(content)
    except (TypeError,ValueError,UnicodeError):
        raise ModelContentJsonInvalid from None
