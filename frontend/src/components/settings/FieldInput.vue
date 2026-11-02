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
			<!-- an image: uploaded as a public file (the install icon is fetched without a login) -->
			<div v-else-if="field.fieldtype === 'Attach Image'" class="flex items-center gap-3">
				<span
					class="grid size-12 shrink-0 place-items-center overflow-hidden rounded-xl border border-outline-gray-2 bg-surface-gray-1"
				>
					<img
						v-if="modelValue"
						:src="modelValue"
						class="size-full object-contain"
						alt=""
					/>
					<LucideImage v-else class="size-5 text-ink-gray-4" />
				</span>
				<FileUploader
					file-types="image/*"
					:upload-args="{ private: false }"
					@success="(file) => emit('update:modelValue', file.file_url)"
				>
					<template #default="{ uploading, progress, error, openFileSelector }">
						<span v-if="error" class="mr-2 text-xs text-ink-red-6">Not uploaded.</span>
						<Button
							size="sm"
							:loading="uploading"
							:label="
								uploading
									? `Uploading ${progress}%`
									: modelValue
									? 'Change'
									: 'Upload'
							"
							@click="openFileSelector"
						/>
					</template>
				</FileUploader>
				<Button
					v-if="modelValue"
					size="sm"
					variant="ghost"
					label="Remove"
					@click="emit('update:modelValue', '')"
				/>
			</div>
			<div v-else-if="field.fieldtype === 'Color'" class="flex items-center gap-2">
				<!-- swatches in an in-app popover (not the browser's colour box), or any hex code beside -->
				<Popover>
					<template #target="{ togglePopover }">
						<button
							class="relative grid size-8 shrink-0 place-items-center overflow-hidden rounded-lg border border-outline-gray-2"
							:style="{ background: modelValue || 'transparent' }"
							aria-label="Pick a colour"
							@click="togglePopover()"
						>
							<LucidePipette v-if="!modelValue" class="size-4 text-ink-gray-5" />
						</button>
					</template>
					<template #body="{ close }">
						<div
							class="mt-1 grid w-56 grid-cols-6 gap-1.5 rounded-xl border border-outline-gray-2 bg-surface-base p-2.5 shadow-xl"
						>
							<button
								v-for="c in SWATCHES"
								:key="c"
								class="size-7 rounded-lg ring-offset-1 ring-offset-surface-base transition hover:scale-110"
								:class="modelValue === c ? 'ring-2 ring-outline-gray-5' : ''"
								:style="{ background: c }"
								:aria-label="c"
								@click="
									emit('update:modelValue', c);
									close();
								"
							/>
						</div>
					</template>
				</Popover>
				<FormControl
					class="w-32"
					placeholder="#2f6f5e"
					:model-value="modelValue"
					@update:model-value="(v) => emit('update:modelValue', v)"
				/>
				<Button
					v-if="modelValue"
					size="sm"
					variant="ghost"
					label="Use standard"
					@click="emit('update:modelValue', '')"
				/>
			</div>
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
import { Button, FileUploader, FormControl, Popover } from "frappe-ui";
import LucideImage from "~icons/lucide/image";
import LucidePipette from "~icons/lucide/pipette";
import { fromNow } from "@/utils/format";
import Picker from "@/components/settings/Picker.vue";

const props = defineProps({
	field: { type: Object, required: true },
	modelValue: { type: [String, Number, Array, Boolean], default: null },
});
const emit = defineEmits(["update:modelValue"]);

// Brand colours that keep white text on the main buttons readable
const SWATCHES = [
	"#1d5fa8",
	"#2563eb",
	"#0e7490",
	"#0f766e",
	"#2f6f5e",
	"#15803d",
	"#4d7c0f",
	"#a16207",
	"#c2410c",
	"#b91c1c",
	"#be185d",
	"#9333ea",
	"#6d28d9",
	"#4338ca",
	"#334155",
	"#57534e",
	"#7c2d12",
	"#111827",
];

const inputType = computed(
	() =>
		({ Int: "number", Float: "number", Time: "time", Datetime: "datetime", Date: "date" }[
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
