<template>
	<div>
		<FormControl
			v-if="field.fieldtype === 'Check'"
			type="checkbox"
			:label="field.label"
			:model-value="!!modelValue"
			:disabled="!!field.read_only"
			@update:model-value="(v) => emit('update:modelValue', v ? 1 : 0)"
		/>
		<template v-else>
			<label class="mb-1 block text-xs text-ink-gray-5">
				{{ field.label }}<span v-if="field.reqd" class="text-ink-red-8"> *</span>
			</label>
			<!-- shown, not edited: plain text, so it does not look like a box to type in -->
			<div v-if="field.read_only" class="text-sm text-ink-gray-8">{{ shown }}</div>
			<Picker
				v-else-if="field.fieldtype === 'Link'"
				:doctype="field.options"
				:model-value="modelValue"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<Picker
				v-else-if="
					['Table MultiSelect'].includes(field.fieldtype) &&
					field.child?.fieldtype === 'Link'
				"
				multiple
				:doctype="field.child.options"
				:model-value="modelValue || []"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<div
				v-else-if="field.fieldtype === 'Table' && field.child?.fieldtype === 'Select'"
				class="grid grid-cols-1 gap-x-4 gap-y-1.5 sm:grid-cols-2 lg:grid-cols-3"
			>
				<FormControl
					v-for="o in selectOptions(field.child.options)"
					:key="o"
					type="checkbox"
					:label="o"
					:model-value="(modelValue || []).includes(o)"
					@update:model-value="(on) => toggle(o, on)"
				/>
			</div>
			<FormControl
				v-else-if="field.fieldtype === 'Select'"
				type="select"
				:options="selectOptions(field.options)"
				:model-value="modelValue"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<FormControl
				v-else-if="['Small Text', 'Text', 'Long Text', 'Code'].includes(field.fieldtype)"
				type="textarea"
				:rows="3"
				:model-value="modelValue"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<FormControl
				v-else-if="field.fieldtype === 'Password'"
				type="password"
				:placeholder="field.is_set ? 'Saved. Type a new one to change it.' : 'Not set'"
				:model-value="modelValue"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<FormControl
				v-else
				:type="inputType"
				:model-value="modelValue"
				@update:model-value="(v) => emit('update:modelValue', v)"
			/>
			<p v-if="field.description" class="mt-1 text-xs text-ink-gray-5">
				{{ field.description }}
			</p>
		</template>
	</div>
</template>

<script setup>
import { computed } from "vue";
import dayjs from "dayjs";
import { FormControl } from "frappe-ui";
import { fromNow } from "@/utils/format";
import Picker from "@/components/settings/Picker.vue";

const props = defineProps({
	field: { type: Object, required: true },
	modelValue: { type: [String, Number, Array, Boolean], default: null },
});
const emit = defineEmits(["update:modelValue"]);

const inputType = computed(
	() =>
		({ Int: "number", Float: "number", Time: "time", Datetime: "text", Date: "date" }[
			props.field.fieldtype
		] || "text")
);
const shown = computed(() => {
	const v = props.modelValue;
	if (Array.isArray(v)) return v.length ? v.join(", ") : "—";
	if (v == null || v === "") return "—";
	// a stored date and time, written for people: "13 Oct 2026, 10:51 AM · 11 days ago"
	if (typeof v === "string" && /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(v))
		return `${dayjs(v).format("D MMM YYYY, h:mm A")} · ${fromNow(v)}`;
	return v;
});

function selectOptions(options) {
	return (options || "").split("\n").filter((o) => o !== "");
}

function toggle(option, on) {
	const now = new Set(props.modelValue || []);
	on ? now.add(option) : now.delete(option);
	emit("update:modelValue", [...now]);
}
</script>
