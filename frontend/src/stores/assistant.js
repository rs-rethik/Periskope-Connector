import { reactive } from "vue";

// The Assistant panel (components/AssistantPanel.vue): open from the sidebar, and its conversation,
// kept while the page is open even when the panel is closed.
export const assistant = reactive({ open: false, turns: [], busy: false });
