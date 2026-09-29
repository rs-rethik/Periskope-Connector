"""Which WhatsApp texts are too small to be worth analysing or answering."""

import re

# Replies that close a conversation and need no answer.
ACKNOWLEDGEMENTS = {
	"ok", "okay", "k", "kk", "thanks", "thank you", "thankyou", "thx", "ty", "tq", "noted",
	"sure", "fine", "yes", "no", "yep", "nope", "done", "great", "nice", "cool", "welcome",
	"got it", "alright", "all right", "good", "hmm", "ok sir", "okay sir", "yes sir", "thanks sir",
	"thank you sir", "ok thanks", "ok thank you", "ok done", "will do", "thanks a lot",
	"thank you so much", "received", "confirmed",
}  # fmt: skip

GREETINGS = {
	"hi", "hello", "hey", "hii", "hiii", "hlo", "good morning", "good afternoon", "good evening",
	"good night", "gm", "gn",
}  # fmt: skip


def plain_text(body: str | None) -> str:
	"""Lower-case text with punctuation and emoji removed."""
	return re.sub(r"[^\w\s]", "", (body or "").lower(), flags=re.UNICODE).strip()


def is_acknowledgement(body: str | None) -> bool:
	"""'ok', 'thanks', emoji-only and 1-2 character texts: nothing to answer."""
	plain = plain_text(body)
	return not plain or len(plain) <= 2 or plain in ACKNOWLEDGEMENTS


def is_single_word_statement(body: str | None) -> bool:
	"""One word and no question mark ('done', 'received'). 'Price?' is kept: it is a question."""
	return "?" not in (body or "") and len(plain_text(body).split()) == 1


def analysis_skip_reason(body: str | None) -> str | None:
	"""Why a text is not sent to the AI (it would only cost tokens), or None if it should be."""
	if not (body or "").strip():
		return "No text in the message"
	plain = plain_text(body)
	if is_acknowledgement(body) or plain in GREETINGS:
		return "Too short to tag"
	if is_single_word_statement(body):
		return "One-word message"
	return None
