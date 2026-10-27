// A message's file, served by the inbox API (the Periskope link never reaches the browser).
export function mediaUrl(message) {
	return `/api/method/periskope_connector.api.inbox.media?message=${encodeURIComponent(
		message.name
	)}`;
}
