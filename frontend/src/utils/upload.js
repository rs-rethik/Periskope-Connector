// Upload a file the user picked as a private File (for a reply or a new chat). Returns
// { name, file_name } of the saved File; throws when it is too large or the upload fails.
export const MAX_UPLOAD_BYTES = 15 * 1024 * 1024;

export async function uploadPrivate(picked) {
	if (picked.size > MAX_UPLOAD_BYTES) throw new Error("The file is larger than 15 MB.");
	const form = new FormData();
	form.append("file", picked, picked.name);
	form.append("is_private", "1");
	form.append("folder", "Home/Attachments");
	const r = await fetch("/api/method/upload_file", {
		method: "POST",
		body: form,
		headers: { "X-Frappe-CSRF-Token": window.csrf_token },
	});
	const data = await r.json().catch(() => ({}));
	if (!r.ok || !data.message) throw new Error("The file could not be uploaded.");
	return { name: data.message.name, file_name: data.message.file_name };
}
