<template>
	<div class="flex h-full flex-col overflow-y-auto bg-surface-base">
		<div v-if="chat" class="flex flex-col gap-5 p-4">
			<section class="flex flex-col gap-3 border-b border-outline-gray-1 pb-4">
				<div class="flex items-center gap-3 pr-8">
					<InitialsAvatar
						size="lg"
						:name="chat.chat_name || chat.contact_number || chat.title"
					/>
					<div class="min-w-0">
						<div class="truncate text-lg font-semibold text-ink-gray-9">
							{{ chat.title }}
						</div>
						<div class="truncate text-sm text-ink-gray-6">
							<span v-if="chat.contact_number">+{{ chat.contact_number }}</span>
							<span v-if="chat.is_group">WhatsApp group</span>
							· on {{ chat.number_label }}
						</div>
					</div>
				</div>
				<div class="flex flex-wrap gap-1.5">
					<Tooltip v-if="record && !record.hidden" text="Open it in the CRM">
						<a
							:href="record.route"
							target="_blank"
							class="cp-chip bg-surface-gray-2 text-ink-gray-8 hover:bg-surface-gray-3"
						>
							<span
								class="size-2 rounded-full"
								:style="{ background: statusColor }"
							/>
							{{ kindLabel }} · {{ record.status }}
						</a>
					</Tooltip>
					<Tooltip
						v-else-if="ctx && !record"
						text="Link this chat to a lead or deal below"
					>
						<span class="cp-chip bg-surface-amber-1 text-ink-amber-8">Not in CRM</span>
					</Tooltip>
					<SlaBadge v-if="chat.sla && chat.sla !== 'On Track'" :chat="chat" />
					<Tooltip
						v-if="ctx && ctx.tags.length"
						:text="`AI tag on ${ctx.tags[0].count} message${
							ctx.tags[0].count === 1 ? '' : 's'
						}`"
					>
						<span class="cp-chip bg-surface-blue-1 text-ink-blue-7">
							<LucideSparkles class="size-3" />{{ ctx.tags[0].tag }}
						</span>
					</Tooltip>
					<Tooltip
						:text="
							chat.last_message_direction === 'Incoming'
								? 'The customer wrote last'
								: 'We wrote last'
						"
					>
						<span class="cp-chip bg-surface-green-1 text-ink-green-8">
							<LucideMessageCircle class="size-3" />{{
								chat.last_message_direction === "Incoming"
									? "Waiting on us"
									: "Answered"
							}}
						</span>
					</Tooltip>
				</div>
				<div class="flex gap-1.5">
					<Tooltip v-if="record && !record.hidden" :text="`Open the ${kind} in the CRM`">
						<a :href="record.route" target="_blank" class="cp-icon"
							><LucideExternalLink class="size-4"
						/></a>
					</Tooltip>
				</div>
			</section>

			<!-- a group's company: its messages follow the organization's deals and leads -->
			<PanelSection v-if="ctx && ctx.is_group" id="organization" title="Organization">
				<div
					v-if="ctx.organization"
					class="flex flex-col gap-2.5 rounded-xl border border-outline-gray-1 p-3"
				>
					<div class="flex items-center gap-2.5">
						<span
							class="grid size-8 shrink-0 place-items-center rounded-lg bg-surface-blue-1 text-ink-blue-7"
						>
							<LucideBuilding2 class="size-4" />
						</span>
						<div class="min-w-0 flex-1">
							<a
								:href="ctx.organization.route"
								target="_blank"
								class="block truncate text-sm font-medium text-ink-gray-9 hover:underline"
								>{{ ctx.organization.title }}</a
							>
							<div class="truncate text-xs text-ink-gray-5">
								{{ ctx.organization.deals }}
								{{ ctx.organization.deals === 1 ? "deal" : "deals" }} ·
								{{ ctx.organization.leads }}
								{{ ctx.organization.leads === 1 ? "lead" : "leads" }}
								<template v-if="ctx.organization.territory">
									· {{ ctx.organization.territory }}</template
								>
							</div>
						</div>
					</div>
					<div v-if="ctx.organization.groups.length" class="text-xs text-ink-gray-6">
						<span class="font-medium">Its other groups:</span>
						{{ ctx.organization.groups.join(", ") }}
					</div>
					<div class="flex gap-2">
						<Button size="sm" label="Change" @click="openOrg" />
						<Button
							size="sm"
							variant="ghost"
							label="Remove link"
							:loading="busy"
							@click="run('unlink_organization')"
						/>
					</div>
				</div>
				<div v-else class="flex flex-col items-start gap-2">
					<span class="text-sm text-ink-gray-5"
						>Link this group to its customer's organization, so its messages go to that
						company's deals and leads.</span
					>
					<Button size="sm" label="Link organization" @click="openOrg">
						<template #prefix><LucideBuilding2 class="size-3.5" /></template>
					</Button>
				</div>
			</PanelSection>

			<!-- What happens next: the earliest open task, with the actions on it -->
			<section v-if="record && !record.hidden">
				<div
					class="mb-1.5 text-[11px] font-semibold uppercase tracking-wider text-ink-gray-5"
				>
					Next step
				</div>
				<div class="rounded-xl border border-outline-gray-1 bg-surface-gray-1 p-3">
					<template v-if="nextTask">
						<div class="flex items-start justify-between gap-2">
							<div class="text-base font-semibold" :class="dueTone(nextTask)">
								{{ dueDay(nextTask) }}
							</div>
							<span
								class="cp-chip"
								:class="priorityTone[nextTask.priority] || priorityTone.Medium"
							>
								<span class="size-1.5 rounded-full bg-current" />{{
									nextTask.priority
								}}
							</span>
						</div>
						<div class="mt-0.5 text-sm text-ink-gray-7">
							<span v-if="dueTime(nextTask)">{{ dueTime(nextTask) }} · </span
							>{{ nextTask.title }}
						</div>
						<div
							v-if="nextTask.assigned_to"
							class="mt-2 flex items-center gap-2 text-sm text-ink-gray-6"
						>
							<InitialsAvatar
								size="xs"
								:name="nameFromEmail(nextTask.assigned_to)"
							/>
							{{ nameFromEmail(nextTask.assigned_to) }}
						</div>
						<div
							v-if="record.tasks.length > 1"
							class="mt-1 flex items-center gap-1.5 text-xs text-ink-gray-5"
						>
							<LucideListChecks class="size-3.5" />
							{{ record.tasks.length - 1 }} more open task{{
								record.tasks.length > 2 ? "s" : ""
							}}
						</div>
						<div class="mt-3 flex flex-wrap gap-2">
							<Button
								size="sm"
								variant="solid"
								theme="green"
								class="!rounded-full"
								:loading="busy"
								@click="run('complete_task', { task: nextTask.name })"
							>
								<template #prefix><LucideCheck class="size-3.5" /></template>
								Done
							</Button>
							<Popover placement="bottom-start">
								<template #target="{ togglePopover }">
									<Button
										size="sm"
										class="!rounded-full"
										@click="
											moveTo = toInput(nextTask.due_date);
											togglePopover();
										"
									>
										<template #prefix
											><LucideCalendarClock class="size-3.5"
										/></template>
										Reschedule
									</Button>
								</template>
								<template #body="{ close }">
									<div
										class="mt-1 flex w-64 flex-col gap-2 rounded-xl border border-outline-gray-2 bg-surface-base p-3 shadow-xl"
									>
										<div class="text-sm font-medium text-ink-gray-8">
											Move to
										</div>
										<DateTimePicker
											v-model="moveTo"
											placeholder="Pick a date and time"
										/>
										<div class="flex flex-wrap gap-1">
											<button
												v-for="q in quickDates"
												:key="q.label"
												class="rounded-full bg-surface-gray-2 px-2 py-0.5 text-xs text-ink-gray-7 hover:bg-surface-gray-3"
												@click="moveTo = q.value()"
											>
												{{ q.label }}
											</button>
										</div>
										<Button
											variant="solid"
											label="Save"
											:disabled="!moveTo"
											:loading="busy"
											@click="reschedule(nextTask, close)"
										/>
									</div>
								</template>
							</Popover>
							<Button
								size="sm"
								class="!rounded-full"
								label="Details"
								@click="openUrl(record.route)"
							/>
						</div>
					</template>
					<div v-else class="flex flex-col items-start gap-2">
						<div class="text-sm text-ink-gray-6">
							No open task. Plan what happens next.
						</div>
					</div>
				</div>
				<Button
					class="mt-3 w-full !rounded-full"
					variant="solid"
					@click="
						error = '';
						taskDialog = true;
					"
				>
					<template #prefix><LucideCalendarPlus class="size-4" /></template>
					Add follow-up
				</Button>
			</section>

			<!-- The lead or deal -->
			<section>
				<div
					class="mb-1 flex items-center text-[11px] font-semibold uppercase tracking-wider text-ink-gray-5"
				>
					CRM
					<Button
						v-if="record && !record.hidden"
						class="ml-auto"
						size="sm"
						variant="ghost"
						label="Change"
						@click="openLink"
					/>
				</div>
				<div v-if="loading && !ctx" class="py-2"><LoadingIndicator class="size-4" /></div>
				<div
					v-else-if="loadError && !ctx"
					class="flex items-center gap-2 text-sm text-ink-gray-6"
				>
					Could not load the CRM record.
					<Button size="sm" label="Retry" @click="load" />
				</div>
				<template v-else-if="record && record.hidden">
					<div class="text-sm text-ink-gray-6">
						Linked to a {{ kind }} you cannot open.
					</div>
				</template>
				<div
					v-else-if="record"
					class="rounded-xl border border-outline-gray-1 bg-surface-base p-3"
				>
					<div class="flex items-start gap-2">
						<div class="min-w-0 flex-1">
							<a
								:href="record.route"
								target="_blank"
								class="flex items-center gap-1 text-base font-medium text-ink-gray-9 hover:underline"
							>
								<span class="truncate">{{ record.title }}</span>
								<LucideExternalLink class="size-3 shrink-0" />
							</a>
							<div class="text-xs text-ink-gray-5">
								{{ kindLabel }} · {{ record.name }}
							</div>
						</div>
					</div>
					<div class="mt-2">
						<Dropdown v-if="record.can_edit" :options="statusOptions">
							<Button size="sm" :label="record.status">
								<template #prefix
									><span
										class="size-2 rounded-full"
										:style="{ background: statusColor }"
								/></template>
							</Button>
						</Dropdown>
						<Badge v-else :label="record.status" variant="subtle" />
					</div>
					<dl class="mt-3 grid grid-cols-[auto,1fr] gap-x-3 gap-y-1 text-sm">
						<dt class="text-ink-gray-5">Owner</dt>
						<dd class="truncate text-ink-gray-8">{{ record.owner_name || "—" }}</dd>
						<template v-if="record.value">
							<dt class="text-ink-gray-5">Value</dt>
							<dd class="text-ink-gray-8">
								{{ money(record.value, record.currency) }}
							</dd>
						</template>
						<template v-if="record.organization">
							<dt class="text-ink-gray-5">Company</dt>
							<dd class="truncate text-ink-gray-8">{{ record.organization }}</dd>
						</template>
						<template v-if="record.email">
							<dt class="text-ink-gray-5">Email</dt>
							<dd class="truncate text-ink-gray-8">{{ record.email }}</dd>
						</template>
						<dt class="text-ink-gray-5">Updated</dt>
						<dd class="text-ink-gray-8">{{ fromNow(record.modified) }}</dd>
					</dl>
					<ErrorMessage
						v-if="
							error &&
							!linkDialog &&
							!createDialog &&
							!noteDialog &&
							!taskDialog &&
							!lostDialog
						"
						class="mt-2"
						:message="error"
					/>
					<button
						v-if="ctx.linked_by_hand"
						class="mt-2 text-xs text-ink-gray-5 hover:text-ink-gray-8 hover:underline"
						@click="unlink"
					>
						Linked by hand · undo
					</button>
				</div>
				<div v-else class="flex flex-col gap-2">
					<div class="text-sm text-ink-gray-5">Not linked to a lead or deal.</div>
					<div class="flex gap-2">
						<Button size="sm" label="Link existing" @click="openLink" />
						<Button
							size="sm"
							variant="solid"
							label="Create lead"
							@click="openCreate"
						/>
					</div>
				</div>

				<!-- the other leads and deals of this customer (or group); one click makes the chat follow one -->
				<div v-if="others.length" class="mt-3">
					<div
						class="mb-1.5 flex items-center gap-1.5 text-xs font-medium text-ink-gray-6"
					>
						<LucideLayers class="size-3.5" />
						{{ others.length }} more
						{{ others.length === 1 ? "record" : "records" }} for
						{{ ctx.is_group ? "this group" : "this customer" }}
					</div>
					<div class="flex flex-col gap-1.5">
						<div
							v-for="r in others"
							:key="r.doctype + r.name"
							class="flex items-center gap-2.5 rounded-xl border border-outline-gray-1 bg-surface-base p-2.5 transition hover:border-outline-gray-2 hover:shadow-sm"
							:class="r.closed ? 'opacity-70' : ''"
						>
							<span
								class="grid size-7 shrink-0 place-items-center rounded-lg"
								:class="
									r.doctype === 'CRM Deal'
										? 'bg-surface-violet-1 text-ink-violet-7'
										: 'bg-surface-amber-1 text-ink-amber-7'
								"
							>
								<component
									:is="
										r.doctype === 'CRM Deal'
											? LucideHandshake
											: LucideUserRound
									"
									class="size-3.5"
								/>
							</span>
							<a :href="r.route" target="_blank" class="min-w-0 flex-1">
								<span class="block truncate text-sm font-medium text-ink-gray-8">{{
									r.title
								}}</span>
								<span
									class="flex items-center gap-1 truncate text-xs text-ink-gray-5"
								>
									<span
										class="size-1.5 shrink-0 rounded-full"
										:style="{ background: dotColor(r.color) }"
									/>
									{{ r.status }} · {{ r.name }}
									<Tooltip
										v-if="r.from_message"
										text="Made from a message in this chat"
										><LucideMessageSquareQuote
											class="size-3 shrink-0 text-ink-violet-6"
									/></Tooltip>
								</span>
							</a>
							<Tooltip text="Make this chat follow it">
								<button
									class="cp-add shrink-0"
									:disabled="busy"
									@click="follow(r)"
								>
									Follow
								</button>
							</Tooltip>
						</div>
					</div>
				</div>
			</section>

			<PanelSection v-if="record && record.summary" id="summary" title="WhatsApp summary">
				<div
					class="flex flex-col gap-2.5 rounded-xl border border-outline-gray-1 bg-surface-gray-1 p-3"
				>
					<div v-for="(part, i) in summaryParts" :key="i">
						<div
							v-if="part.heading"
							class="mb-0.5 flex items-center gap-1.5 text-xs font-semibold"
							:class="part.tone"
						>
							<span class="size-1.5 rounded-full bg-current" />{{ part.heading }}
						</div>
						<div class="text-sm leading-snug text-ink-gray-7">{{ part.text }}</div>
					</div>
				</div>
			</PanelSection>

			<PanelSection
				v-if="record && !record.hidden"
				id="tasks"
				:title="`Open tasks · ${record.tasks.length}`"
			>
				<template #actions>
					<button
						class="cp-add"
						@click="
							error = '';
							taskDialog = true;
						"
					>
						<LucidePlus class="size-3.5" />Add
					</button>
				</template>
				<div v-if="!record.tasks.length" class="cp-empty">No open tasks.</div>
				<div class="flex flex-col gap-1.5">
					<div
						v-for="t in record.tasks"
						:key="t.name"
						class="group flex items-start gap-2.5 rounded-xl border border-outline-gray-1 bg-surface-base p-2.5 transition hover:border-outline-gray-2 hover:shadow-sm"
					>
						<Tooltip text="Mark done">
							<button
								aria-label="Mark done"
								class="tap mt-0.5 grid size-5 shrink-0 place-items-center rounded-full border border-outline-gray-3 text-transparent transition hover:border-outline-green-2 hover:bg-surface-green-2 hover:text-ink-green-7"
								:disabled="busy"
								@click="run('complete_task', { task: t.name })"
							>
								<LucideCheck class="size-3" />
							</button>
						</Tooltip>
						<div class="min-w-0 flex-1">
							<div class="text-sm font-medium text-ink-gray-8">{{ t.title }}</div>
							<div class="mt-1 flex flex-wrap items-center gap-1">
								<Tooltip
									v-if="t.due_date"
									:text="dayjs(t.due_date).format('D MMM YYYY, h:mm A')"
									><span class="cp-chip" :class="dueChip(t)">
										<LucideCalendar class="size-3" />{{ dueDay(t)
										}}<template v-if="dueTime(t)">
											· {{ dueTime(t) }}</template
										>
									</span></Tooltip
								>
								<span
									class="cp-chip"
									:class="priorityTone[t.priority] || priorityTone.Medium"
									>{{ t.priority }}</span
								>
								<Tooltip v-if="t.assigned_to" :text="t.assigned_to"
									><span class="cp-chip bg-surface-gray-2 text-ink-gray-7">
										<InitialsAvatar
											size="xs"
											:name="nameFromEmail(t.assigned_to)"
										/>{{ nameFromEmail(t.assigned_to).split(" ")[0] }}
									</span></Tooltip
								>
							</div>
						</div>
					</div>
				</div>
			</PanelSection>

			<PanelSection
				v-if="record && !record.hidden"
				id="notes"
				:title="`CRM notes · ${record.notes.length}`"
			>
				<template #actions>
					<button
						class="cp-add"
						@click="
							error = '';
							noteDialog = true;
						"
					>
						<LucidePlus class="size-3.5" />Add
					</button>
				</template>
				<div v-if="!record.notes.length" class="cp-empty">No notes yet.</div>
				<div class="flex flex-col gap-1.5">
					<div
						v-for="n in record.notes"
						:key="n.name"
						class="rounded-xl border border-outline-gray-1 bg-surface-base p-2.5 transition hover:border-outline-gray-2 hover:shadow-sm"
					>
						<div class="flex items-start gap-2">
							<LucideStickyNote class="mt-0.5 size-3.5 shrink-0 text-ink-amber-7" />
							<div class="min-w-0 flex-1 text-sm font-medium text-ink-gray-8">
								{{ n.title }}
							</div>
							<Tooltip :text="dayjs(n.creation).format('D MMM YYYY, h:mm A')"
								><span class="shrink-0 text-xs text-ink-gray-5">{{
									fromNow(n.creation)
								}}</span></Tooltip
							>
						</div>
						<div
							class="mt-1 line-clamp-3 pl-[22px] text-sm leading-snug text-ink-gray-6"
						>
							{{ plain(n.content) }}
						</div>
					</div>
				</div>
			</PanelSection>

			<PanelSection v-if="ctx && ctx.tags.length" id="tags" title="AI tags">
				<div class="flex flex-wrap gap-1.5">
					<Tooltip
						v-for="t in ctx.tags"
						:key="t.tag"
						:text="`${t.count} message${t.count === 1 ? '' : 's'}`"
						><span class="cp-chip bg-surface-blue-1 text-ink-blue-7">
							<LucideSparkles class="size-3" />{{ t.tag
							}}<span v-if="t.count > 1" class="text-ink-blue-5"
								>· {{ t.count }}</span
							>
						</span></Tooltip
					>
				</div>
			</PanelSection>

			<PanelSection
				v-if="ctx && ctx.other_chats.length"
				id="other-numbers"
				title="On our other numbers"
			>
				<div class="flex flex-col gap-1.5">
					<button
						v-for="o in ctx.other_chats"
						:key="o.name"
						class="flex w-full items-center gap-2.5 rounded-xl border border-outline-gray-1 bg-surface-base p-2.5 text-left transition hover:border-outline-gray-2 hover:shadow-sm"
						@click="$router.push({ name: 'Inbox', params: { chat: o.name } })"
					>
						<span
							class="grid size-7 shrink-0 place-items-center rounded-full bg-surface-green-2 text-ink-green-8"
						>
							<LucidePhone class="size-3.5" />
						</span>
						<span class="min-w-0 flex-1">
							<span class="block text-sm font-medium text-ink-gray-8">{{
								o.number_label
							}}</span>
							<span class="block truncate text-xs text-ink-gray-5">{{
								o.last_message_preview
							}}</span>
						</span>
						<LucideChevronRight class="size-4 shrink-0 text-ink-gray-4" />
					</button>
				</div>
			</PanelSection>

			<section>
				<div class="text-[11px] font-semibold uppercase tracking-wider text-ink-gray-5">
					Assigned to
				</div>
				<div class="mt-1.5 flex flex-wrap gap-1.5">
					<span
						v-for="u in chat.assigned_to || []"
						:key="u"
						class="cp-chip bg-surface-gray-2 text-ink-gray-8"
					>
						<InitialsAvatar size="xs" :name="nameFromEmail(u)" />{{ nameFromEmail(u) }}
					</span>
					<span v-if="!chat.assigned_to?.length" class="text-sm text-ink-gray-5"
						>Nobody</span
					>
				</div>
			</section>
		</div>

		<!-- Link to an existing lead or deal -->
		<Dialog v-model="orgDialog" :options="{ title: 'Link group to an organization' }">
			<template #body-content>
				<TextInput
					v-model="orgQuery"
					placeholder="Search organization name"
					:debounce="300"
				/>
				<div class="mt-2 max-h-72 overflow-y-auto">
					<button
						v-for="o in orgResults"
						:key="o.name"
						class="flex w-full items-center gap-2.5 rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
						:disabled="busy"
						@click="linkOrg(o)"
					>
						<LucideBuilding2 class="size-4 shrink-0 text-ink-gray-5" />
						<span class="min-w-0 flex-1">
							<span class="block truncate text-sm text-ink-gray-8">{{
								o.title
							}}</span>
							<span class="block truncate text-xs text-ink-gray-5"
								>{{ o.deals }} {{ o.deals === 1 ? "deal" : "deals" }} ·
								{{ o.groups }} WhatsApp {{ o.groups === 1 ? "group" : "groups"
								}}<template v-if="o.territory">
									· {{ o.territory }}</template
								></span
							>
						</span>
						<LucideCheck
							v-if="ctx?.organization?.name === o.name"
							class="size-4 text-ink-green-7"
						/>
					</button>
					<div
						v-if="orgSearching && !orgResults.length"
						class="p-2 text-sm text-ink-gray-5"
					>
						Searching…
					</div>
					<div v-else-if="!orgResults.length" class="p-2 text-sm text-ink-gray-5">
						No organization found. Try part of the company's name.
					</div>
				</div>
				<ErrorMessage v-if="error && orgDialog" class="mt-2" :message="error" />
			</template>
		</Dialog>

		<Dialog v-model="linkDialog" :options="{ title: 'Link to a lead or deal' }">
			<template #body-content>
				<TextInput
					v-model="query"
					placeholder="Search name, company, number or email"
					:debounce="300"
				/>
				<div class="mt-2 max-h-72 overflow-y-auto">
					<button
						v-for="r in results"
						:key="r.doctype + r.name"
						class="flex w-full flex-col rounded px-2 py-1.5 text-left hover:bg-surface-gray-2"
						:disabled="busy"
						@click="link(r)"
					>
						<span class="text-sm text-ink-gray-8">
							{{ r.title }}
							<span class="text-xs text-ink-gray-5"
								>· {{ r.doctype === "CRM Deal" ? "Deal" : "Lead" }}</span
							>
						</span>
						<span class="truncate text-xs text-ink-gray-5">{{ r.subtitle }}</span>
					</button>
					<div v-if="searching && !results.length" class="p-2 text-sm text-ink-gray-5">
						Searching…
					</div>
					<div v-else-if="!results.length" class="flex flex-col items-start gap-2 p-2">
						<span class="text-sm text-ink-gray-5">
							Nothing found. Try a name, company or the last digits of the number.
						</span>
						<Button
							v-if="!record"
							size="sm"
							label="Create a new lead instead"
							@click="
								linkDialog = false;
								openCreate();
							"
						/>
					</div>
				</div>
				<ErrorMessage class="mt-2" :message="error" />
			</template>
		</Dialog>

		<!-- Create a lead -->
		<Dialog v-model="createDialog" :options="{ title: 'Create lead' }">
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="create">
					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="lead.first_name" label="First name" autofocus />
						<FormControl v-model="lead.last_name" label="Last name" />
					</div>
					<FormControl v-model="lead.organization" label="Company" />
					<FormControl v-model="lead.email" label="Email" />
					<div v-if="chat?.contact_number" class="text-sm text-ink-gray-5">
						Mobile: +{{ chat.contact_number }} · Source: WhatsApp · Owner: you
					</div>
					<ErrorMessage :message="error" />
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Create lead"
					:loading="busy"
					:disabled="!lead.first_name"
					@click="create"
				/>
			</template>
		</Dialog>

		<Dialog v-model="lostDialog" :options="{ title: `Mark as ${lost.status}` }">
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-model="lost.reason"
						type="select"
						label="Why was it lost?"
						:options="reasons"
					/>
					<FormControl
						v-model="lost.notes"
						type="textarea"
						:label="lost.reason === 'Other' ? 'Details (needed for Other)' : 'Details'"
						:rows="3"
					/>
					<ErrorMessage :message="error" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					theme="red"
					:label="`Mark as ${lost.status}`"
					:loading="busy"
					:disabled="!lost.reason || (lost.reason === 'Other' && !lost.notes)"
					@click="markLost"
				/>
			</template>
		</Dialog>

		<Dialog v-model="noteDialog" :options="{ title: 'Add a CRM note' }">
			<template #body-content>
				<div class="flex flex-col gap-3">
					<FormControl
						v-model="note.title"
						label="Title"
						placeholder="Note from WhatsApp"
					/>
					<FormControl
						v-model="note.content"
						type="textarea"
						label="Note"
						:rows="5"
						autofocus
					/>
					<ErrorMessage :message="error" />
				</div>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Add note"
					:loading="busy"
					:disabled="!note.content"
					@click="addNote"
				/>
			</template>
		</Dialog>

		<Dialog v-model="taskDialog" :options="{ title: 'Add a task' }">
			<template #body-content>
				<form class="flex flex-col gap-3" @submit.prevent="addTask">
					<FormControl v-model="task.title" label="Title" autofocus />
					<FormControl
						v-model="task.description"
						type="textarea"
						label="Details"
						:rows="3"
					/>
					<div class="grid grid-cols-2 gap-3">
						<FormControl v-model="task.due_date" type="date" label="Due" />
						<FormControl
							v-model="task.priority"
							type="select"
							label="Priority"
							:options="['Low', 'Medium', 'High']"
						/>
					</div>
					<ErrorMessage :message="error" />
					<button type="submit" class="hidden" />
				</form>
			</template>
			<template #actions>
				<Button
					variant="solid"
					label="Add task"
					:loading="busy"
					:disabled="!task.title"
					@click="addTask"
				/>
			</template>
		</Dialog>
	</div>
</template>

<script setup>
import InitialsAvatar from "@/components/InitialsAvatar.vue";
import PanelSection from "@/components/PanelSection.vue";
import SlaBadge from "@/components/SlaBadge.vue";
import dayjs from "dayjs";
import LucidePhone from "~icons/lucide/phone";
import LucideCheck from "~icons/lucide/check";
import LucideSparkles from "~icons/lucide/sparkles";
import LucideMessageCircle from "~icons/lucide/message-circle";
import LucideListChecks from "~icons/lucide/list-checks";
import LucideCalendarClock from "~icons/lucide/calendar-clock";
import LucideCalendarPlus from "~icons/lucide/calendar-plus";
import LucideCalendar from "~icons/lucide/calendar";
import LucideLayers from "~icons/lucide/layers";
import LucideHandshake from "~icons/lucide/handshake";
import LucideUserRound from "~icons/lucide/user-round";
import LucideMessageSquareQuote from "~icons/lucide/message-square-quote";
import LucideStickyNote from "~icons/lucide/sticky-note";
import LucideChevronRight from "~icons/lucide/chevron-right";
import LucideBuilding2 from "~icons/lucide/building-2";
import { computed, ref, watch } from "vue";
import {
	Badge,
	Button,
	Dialog,
	Dropdown,
	ErrorMessage,
	DateTimePicker,
	FormControl,
	LoadingIndicator,
	Popover,
	TextInput,
	Tooltip,
	call,
} from "frappe-ui";
import LucideExternalLink from "~icons/lucide/external-link";
import LucidePlus from "~icons/lucide/plus";
import { changed, inbox } from "@/stores/inbox";
import { errorText } from "@/utils/errors";
import { fromNow, nameFromEmail } from "@/utils/format";

const props = defineProps({ chat: { type: Object, default: null } });

const ctx = ref(null);
const loading = ref(false);
const loadError = ref(false);
const searching = ref(false);
let loadSeq = 0;
let searchSeq = 0;
const busy = ref(false);
const error = ref("");
const linkDialog = ref(false);
const createDialog = ref(false);
const noteDialog = ref(false);
const taskDialog = ref(false);
const lostDialog = ref(false);
const reasons = ref([]);
const lost = ref({ status: "", reason: "", notes: "" });
const query = ref("");
const results = ref([]);
const lead = ref({ first_name: "", last_name: "", organization: "", email: "" });
const note = ref({ title: "", content: "" });
const task = ref({ title: "", description: "", due_date: "", priority: "Medium" });

const record = computed(() => ctx.value?.record || null);
// The summary's section titles (writeback.summary) shown as headings.
const SUMMARY_HEADINGS = ["What the customer wants", "What we answered", "Still pending"];
const summaryLines = computed(() =>
	(record.value?.summary || "")
		.split("\n")
		.filter((l) => l.trim())
		.map((l) => ({ text: l, heading: SUMMARY_HEADINGS.includes(l.trim()) }))
);
const kind = computed(() => (record.value?.doctype === "CRM Deal" ? "deal" : "lead"));
const kindLabel = computed(() => (record.value?.doctype === "CRM Deal" ? "Deal" : "Lead"));
const statusColor = computed(
	() => record.value?.statuses?.find((s) => s.value === record.value.status)?.color || "gray"
);
const statusOptions = computed(() =>
	(record.value?.statuses || []).map((s) => ({
		label: s.value,
		onClick: () =>
			s.type === "Lost" ? askLost(s.value) : run("set_status", { status: s.value }),
	}))
);

// The text of a note's HTML. DOMParser makes an inert document: nothing in it loads or runs
// (innerHTML on an element would fire an <img onerror> even off the page).
function plain(html) {
	return new DOMParser().parseFromString(html || "", "text/html").body.textContent || "";
}

function money(value, currency) {
	try {
		return new Intl.NumberFormat(undefined, {
			style: "currency",
			currency: currency || "INR",
			maximumFractionDigits: 0,
		}).format(value);
	} catch (e) {
		return value;
	}
}

// Only the newest load fills the panel, so a slow answer for the previous chat never shows here.
async function load() {
	if (!props.chat?.name) return;
	const seq = ++loadSeq;
	loading.value = true;
	try {
		const out = await call("periskope_connector.api.crm_panel.get_context", {
			chat_name: props.chat.name,
		});
		if (seq !== loadSeq) return;
		ctx.value = out;
		loadError.value = false;
	} catch (e) {
		// a failed refresh keeps what is shown; a failed first load says so (not "not linked")
		if (seq === loadSeq) loadError.value = true;
	} finally {
		if (seq === loadSeq) loading.value = false;
	}
}

// One change at a time, so two quick clicks cannot undo each other.
async function run(method, args = {}) {
	if (busy.value) return false;
	busy.value = true;
	error.value = "";
	// what this change returns is the newest record: a load still on its way must not replace it
	const seq = ++loadSeq;
	try {
		const out = await call(`periskope_connector.api.crm_panel.${method}`, {
			chat_name: props.chat.name,
			...args,
		});
		if (seq === loadSeq) ctx.value = out;
		return true;
	} catch (e) {
		error.value = errorText(e, "That did not work.");
		return false;
	} finally {
		busy.value = false;
		if (seq === loadSeq) loading.value = false; // the load it replaced no longer does this
	}
}

async function openLink() {
	error.value = "";
	query.value = props.chat?.chat_name || "";
	linkDialog.value = true;
	await search();
}

async function search() {
	const seq = ++searchSeq;
	searching.value = true;
	try {
		const out = await call("periskope_connector.api.crm_panel.search_records", {
			query: query.value,
		});
		if (seq === searchSeq) results.value = out;
	} catch (e) {
		if (seq === searchSeq) error.value = errorText(e, "That did not work.");
	} finally {
		if (seq === searchSeq) searching.value = false;
	}
}

// --- a group's organization (crm_panel.link_organization) ---
const orgDialog = ref(false);
const orgQuery = ref("");
const orgResults = ref([]);
const orgSearching = ref(false);
let orgSeq = 0;

async function openOrg() {
	error.value = "";
	orgQuery.value = ctx.value?.organization ? "" : props.chat?.chat_name || "";
	orgDialog.value = true;
	await searchOrgs();
	if (!orgResults.value.length && orgQuery.value) {
		orgQuery.value = ""; // the group's name matched nothing: show the recent ones
	}
}

async function searchOrgs() {
	const seq = ++orgSeq;
	orgSearching.value = true;
	try {
		const out = await call("periskope_connector.api.crm_panel.search_organizations", {
			query: orgQuery.value,
		});
		if (seq === orgSeq) orgResults.value = out;
	} catch (e) {
		if (seq === orgSeq) error.value = errorText(e, "That did not work.");
	} finally {
		if (seq === orgSeq) orgSearching.value = false;
	}
}
watch(orgQuery, () => orgDialog.value && searchOrgs());

async function linkOrg(o) {
	if (await run("link_organization", { organization: o.name })) orgDialog.value = false;
}

async function link(r) {
	if (await run("link_record", { doctype: r.doctype, name: r.name })) linkDialog.value = false;
}

function openCreate() {
	error.value = "";
	const [first, ...rest] = (props.chat?.chat_name || "").split(" ");
	lead.value = {
		first_name: first || "",
		last_name: rest.join(" "),
		organization: "",
		email: "",
	};
	createDialog.value = true;
}

async function create() {
	if (!lead.value.first_name) return; // Enter in the form skips the button's own check
	if (await run("create_lead", lead.value)) createDialog.value = false;
}

async function addNote() {
	if (await run("add_note", note.value)) {
		noteDialog.value = false;
		note.value = { title: "", content: "" };
	}
}

async function addTask() {
	if (!task.value.title) return;
	if (await run("add_task", task.value)) {
		taskDialog.value = false;
		task.value = { title: "", description: "", due_date: "", priority: "Medium" };
	}
}

async function askLost(status) {
	error.value = "";
	lost.value = { status, reason: "", notes: "" };
	try {
		if (!reasons.value.length)
			reasons.value = await call("periskope_connector.api.crm_panel.lost_reasons");
	} catch (e) {
		// the reason list stays empty; the dialog still opens and the server says what is missing
	}
	lostDialog.value = true;
}

async function markLost() {
	const ok = await run("set_status", {
		status: lost.value.status,
		lost_reason: lost.value.reason,
		lost_notes: lost.value.notes,
	});
	if (ok) lostDialog.value = false;
}

// --- The header and the next step ---------------------------------------------------------------

// The task to do next: the earliest one with a due date, else the first.
const nextTask = computed(() => {
	const tasks = record.value?.tasks || [];
	return (
		tasks.filter((t) => t.due_date).sort((a, b) => (a.due_date < b.due_date ? -1 : 1))[0] ||
		tasks[0] ||
		null
	);
});

// The summary as headed parts, each heading in its own colour.
const SUMMARY_TONES = {
	"What the customer wants": "text-ink-green-7",
	"What we answered": "text-ink-blue-7",
	"Still pending": "text-ink-amber-8",
};
const summaryParts = computed(() => {
	const parts = [];
	for (const line of summaryLines.value) {
		const text = line.text.trim();
		if (line.heading) parts.push({ heading: text, tone: SUMMARY_TONES[text], text: "" });
		else if (parts.length) {
			const last = parts[parts.length - 1];
			last.text = last.text ? `${last.text} ${text}` : text;
		} else parts.push({ heading: "", text });
	}
	return parts;
});

function dueChip(t) {
	const days = dayjs(t.due_date).startOf("day").diff(dayjs().startOf("day"), "day");
	if (days < 0) return "bg-surface-red-1 text-ink-red-7";
	if (days === 0) return "bg-surface-amber-1 text-ink-amber-8";
	return "bg-surface-gray-2 text-ink-gray-7";
}

const priorityTone = {
	High: "bg-surface-red-1 text-ink-red-7",
	Medium: "bg-surface-amber-1 text-ink-amber-8",
	Low: "bg-surface-gray-2 text-ink-gray-6",
};

function dueDay(t) {
	if (!t.due_date) return "No due date";
	const due = dayjs(t.due_date);
	const days = due.startOf("day").diff(dayjs().startOf("day"), "day");
	if (days < 0) return days === -1 ? "Overdue · yesterday" : `Overdue · ${-days} days`;
	if (days === 0) return "Today";
	if (days === 1) return "Tomorrow";
	return due.format(days < 7 ? "dddd" : "D MMM");
}

function dueTime(t) {
	if (!t.due_date) return "";
	const due = dayjs(t.due_date);
	return due.format("HH:mm") === "00:00" ? "" : due.format("h:mm A");
}

function dueTone(t, fallback = "text-ink-gray-9") {
	if (!t.due_date) return fallback;
	const days = dayjs(t.due_date).startOf("day").diff(dayjs().startOf("day"), "day");
	return days < 0 ? "text-ink-red-6" : days === 0 ? "text-ink-amber-8" : fallback;
}

function openUrl(url) {
	if (url) window.open(url, "_blank");
}

const moveTo = ref("");
const PICKED = "YYYY-MM-DD HH:mm:ss"; // DateTimePicker's value
const toInput = (value) => (value ? dayjs(value).format(PICKED) : "");
const at = (days, hour) => () => dayjs().add(days, "day").hour(hour).minute(0).format(PICKED);
const quickDates = [
	{
		label: "Later today",
		value: () => dayjs().add(2, "hour").minute(0).format(PICKED),
	},
	{ label: "Tomorrow 10 AM", value: at(1, 10) },
	{ label: "In 3 days", value: at(3, 10) },
	{ label: "Next week", value: at(7, 10) },
];
async function reschedule(t, close) {
	if (await run("reschedule_task", { task: t.name, due_date: moveTo.value })) close();
}

// The other leads and deals tied to this chat; the followed one is the card above.
const others = computed(() => (ctx.value?.related || []).filter((r) => !r.active));

const STATUS_DOTS = {
	gray: "#9ca3af",
	blue: "#3b82f6",
	green: "#22c55e",
	red: "#ef4444",
	orange: "#f97316",
	amber: "#f59e0b",
	yellow: "#eab308",
	cyan: "#06b6d4",
	teal: "#14b8a6",
	violet: "#8b5cf6",
	pink: "#ec4899",
	purple: "#a855f7",
	black: "#374151",
};
const dotColor = (color) => STATUS_DOTS[color] || STATUS_DOTS.gray;

function follow(r) {
	run("link_record", { doctype: r.doctype, name: r.name });
}

function unlink() {
	run("unlink_record");
}

watch(query, () => linkDialog.value && search());
watch(
	() => props.chat?.name,
	() => {
		ctx.value = null; // never show the previous chat's record while this one loads
		loadError.value = false;
		load();
	},
	{ immediate: true }
);
watch(
	() => inbox.version,
	() => {
		if (props.chat && changed(props.chat.name)) load();
	}
);
</script>
