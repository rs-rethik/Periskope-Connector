// A message's file, served by the inbox API (the Periskope link never reaches the browser).
export function mediaUrl(message) {
	return `/api/method/periskope_connector.api.inbox.media?message=${encodeURIComponent(
		message.name
	)}`;
}

// A small copy of an image message, for the bubble and the viewer's strip; the viewer itself and
// Download use the full file.
export function thumbUrl(message) {
	return `${mediaUrl(message)}&thumb=1`;
}
