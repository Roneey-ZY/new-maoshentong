"""Record model request attempts without retaining prompts, keys or response text."""
from time import perf_counter


def metered_model(inner, events):
    from agents import Model

    class Metered(Model):
        async def get_response(self, *args, **kwargs):
            start = perf_counter()
            event = {"attempt": len(events) + 1, "status": "running", "input_tokens": None,
                     "output_tokens": None, "cached_input_tokens": None}
            events.append(event)
            try:
                response = await inner.get_response(*args, **kwargs)
                usage = response.usage
                event.update(status="ok", input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                             cached_input_tokens=getattr(usage.input_tokens_details, "cached_tokens", 0))
                return response
            except Exception as exc:
                event.update(status="error", error_type=type(exc).__name__)
                raise
            finally:
                event["ms"] = round((perf_counter() - start) * 1000, 2)

        def stream_response(self, *args, **kwargs):
            raise NotImplementedError("Usage meter supports this application's non-streaming calls only")

        def get_retry_advice(self, request):
            return inner.get_retry_advice(request)

    return Metered()
