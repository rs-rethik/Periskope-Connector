import { ref } from "vue";

// Phone width: one pane at a time, and the sidebar slides in from a menu button.
const query = window.matchMedia("(max-width: 767px)");
export const isMobile = ref(query.matches);
query.addEventListener("change", (e) => (isMobile.value = e.matches));

// Wide enough for the CRM panel beside the chat (Tailwind's xl); narrower screens open it on demand.
const wide = window.matchMedia("(min-width: 1280px)");
export const isWide = ref(wide.matches);
wide.addEventListener("change", (e) => (isWide.value = e.matches));
