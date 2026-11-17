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
# Reserved context for request framing that is not part of the serialized payload.
LOCAL_MODEL_REQUEST_OVERHEAD_BYTES=1024


class ModelRequestFailed(RuntimeError):
    pass

class ModelEnvelopeJsonInvalid(ValueError):
    pass


class ModelContentJsonInvalid(ValueError):
    pass


def compact_json(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def chat_completion_with_payload(system,response_format,payload,payload_name):
    user=compact_json(payload)
    reserved=(len(system.encode('utf-8'))
              + len(json.dumps(response_format).encode('utf-8'))
              + LOCAL_MODEL_REQUEST_OVERHEAD_BYTES
              + LOCAL_MODEL_OUTPUT_TOKEN_LIMIT)
    budget=LOCAL_MODEL_CONTEXT_TOKEN_LIMIT-reserved
    if budget<1: raise RuntimeError(f'{payload_name}_payload_budget')
    if len(user.encode('utf-8'))>budget:
        raise ValueError(f'{payload_name}_payload_size')
    try:
        return chat_completion([
            {'role':'system','content':system},
            {'role':'user','content':user},
        ],response_format)
    except OSError:
        raise ModelRequestFailed from None


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
