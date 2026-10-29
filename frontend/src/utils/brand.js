import { watchEffect } from "vue";
import { boot } from "@/boot";

// Settings > Branding on the page itself: the colour (theme.css reads --brand), the tab's icon and
// title, and the name and icon iOS uses when the app is added to the home screen (the install manifest
// is served by branding.py).
function setLink(rel, href) {
	let el = document.querySelector(`link[rel="${rel}"]`);
	if (!el) {
		el = document.createElement("link");
		el.rel = rel;
		document.head.appendChild(el);
	}
	el.href = href;
}

watchEffect(() => {
	const brand = boot.brand || {};
	const root = document.documentElement;
	if (brand.color) {
		root.style.setProperty("--brand", brand.color);
		root.dataset.brand = "";
	} else {
		root.style.removeProperty("--brand");
		delete root.dataset.brand;
	}
	if (brand.logo) setLink("icon", brand.logo);
	if (brand.app_icon) setLink("apple-touch-icon", brand.app_icon);
	if (brand.app_name)
		document
			.querySelector('meta[name="apple-mobile-web-app-title"]')
			?.setAttribute("content", brand.app_name);
	if (brand.name) document.title = `${brand.name} Inbox`;
});
