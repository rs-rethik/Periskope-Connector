import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import path from "path";

// Built into periskope_connector/public/inbox and served at /whatsapp (www/whatsapp.py),
// the same way Frappe CRM and Helpdesk serve their apps.
export default defineConfig(async () => {
	const { default: frappeui } = await import("frappe-ui/vite");
	return {
		plugins: [
			frappeui({
				frappeProxy: true,
				lucideIcons: true,
				jinjaBootData: true,
				buildConfig: {
					outDir: "../periskope_connector/public/inbox",
					emptyOutDir: true,
					indexHtmlPath: "../periskope_connector/www/whatsapp.html",
				},
			}),
			vue(),
		],
		resolve: {
			alias: {
				"@": path.resolve(import.meta.dirname, "src"),
			},
			dedupe: ["vue", "vue-router", "frappe-ui"],
		},
		optimizeDeps: {
			include: ["feather-icons", "tailwind.config.js"],
		},
	};
});
