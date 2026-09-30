<template>
  <section class="page" data-module="vessel">
    <header class="page-head">
      <div>
        <h2>船舶作业管理</h2>
        <p class="page-desc">
          一条船的作业条目走同一条单向作业链：待开工 → 作业中 → 已核对 → 确认离泊，
          只许顺推、不许跳级倒退；勾选同船条目可整组提交。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记船舶</button>
        <button class="btn" type="button" @click="exportRows">导出作业清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <div class="board-wrap">
      <h3>作业看板（箱量与下方作业明细同源）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>船舶编号</th><th>船名</th><th>航线代码</th><th>船舶状态</th>
            <th>待开工</th><th>作业中</th><th>已核对</th>
            <th>明细箱量</th><th>核对箱量</th><th>离泊条件</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="ship in board" :key="ship.vessel_id" :class="{ active: ship.vessel_id === currentVesselId }">
            <td>{{ ship['船舶编号'] }}</td>
            <td>{{ ship['船名'] }}</td>
            <td>{{ ship['航线代码'] || '—' }}</td>
            <td><span class="tag">{{ ship['船舶状态'] }}</span></td>
            <td>{{ ship['待开工'] }}</td>
            <td>{{ ship['作业中'] }}</td>
            <td>{{ ship['已核对'] }}</td>
            <td>{{ ship['明细箱量'] }}</td>
            <td>{{ ship['核对箱量'] }}</td>
            <td>
              <span :class="ship['可离泊'] ? 'ok-text' : 'muted-text'">
                {{ ship['可离泊'] ? '可离泊' : '未核对完' }}
              </span>
            </td>
            <td>
              <button class="link" type="button" @click="selectVessel(ship.vessel_id)">查看作业条目</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="currentVessel" class="work-wrap">
      <div class="work-head">
        <div>
          <h3>
            {{ currentVessel['船名'] }}（{{ currentVessel['船舶编号'] }}）的作业条目
            <span class="tag">{{ currentVessel['船舶状态'] }}</span>
          </h3>
          <p class="page-desc">
            明细箱量合计 <strong>{{ summary.total }}</strong>，已核对箱量
            <strong>{{ summary.checked }}</strong>，与看板同源。仅可勾选同一条船的条目。
          </p>
        </div>
        <div class="ship-actions">
          <button
            class="btn"
            type="button"
            :disabled="currentVessel['船舶状态'] !== '锚地待泊'"
            @click="runShipAction('安排靠泊')"
          >安排靠泊</button>
          <button
            class="btn"
            type="button"
            :disabled="currentVessel['船舶状态'] !== '靠泊中'"
            @click="runShipAction('开始作业')"
          >开始作业</button>
        </div>
      </div>

      <div class="batch-bar">
        <label class="check-all">
          <input
            type="checkbox"
            :checked="allSelected"
            :indeterminate.prop="someSelected"
            @change="toggleAll"
          />
          全选本船
        </label>
        <span class="muted-text">已选 {{ selectedIds.length }} 条</span>
        <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="submitBatch('确认开工')">整组确认开工</button>
        <button class="btn primary" type="button" :disabled="!selectedIds.length" @click="submitBatch('核对完成')">整组核对完成</button>
        <button
          class="btn danger"
          type="button"
          :disabled="!canDepart"
          :title="canDepart ? '' : '全部条目核对完成后才允许离泊'"
          @click="submitBatch('确认离泊')"
        >确认离泊</button>
      </div>

      <table class="data-table">
        <thead>
          <tr>
            <th style="width: 36px"></th>
            <th v-for="column in workColumns" :key="column">{{ column }}</th>
            <th>作业环节</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="work in works" :key="String(work.id)" :class="statusRowClass(work.status)">
            <td>
              <input
                v-model="selectedIds"
                type="checkbox"
                :value="work.id"
                :disabled="currentVessel['船舶状态'] === '已离泊'"
              />
            </td>
            <td v-for="column in workColumns" :key="column">{{ work[column] ?? '—' }}</td>
            <td><span class="stage" :class="`stage-${stageIndex(work.status)}`">{{ work.status }}</span></td>
            <td class="row-actions">
              <button
                class="link"
                type="button"
                :disabled="work.status === '待开工' || currentVessel['船舶状态'] === '已离泊'"
                @click="rejectOne(work)"
              >打回</button>
            </td>
          </tr>
          <tr v-if="!works.length">
            <td :colspan="workColumns.length + 3" class="empty-state">该船暂无作业条目</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div class="ledger-wrap">
      <h3>船舶作业台账 · 待办清单（离泊确认后回写）</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>船舶编号</th><th>船名</th><th>航线代码</th><th>出口航次</th>
            <th>核对箱量</th><th>条目数</th><th>离泊时间</th><th>待办状态</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in ledger" :key="String(row.id)">
            <td>{{ row['船舶编号'] }}</td>
            <td>{{ row['船名'] }}</td>
            <td>{{ row['航线代码'] }}</td>
            <td>{{ row['出口航次'] }}</td>
            <td>{{ row['核对箱量'] }}</td>
            <td>{{ row['条目数'] }}</td>
            <td>{{ row['离泊时间'] }}</td>
            <td><span class="tag tag-todo">{{ row['待办状态'] }}</span></td>
          </tr>
          <tr v-if="!ledger.length">
            <td colspan="8" class="empty-state">暂无离泊回写的台账待办</td>
          </tr>
        </tbody>
      </table>
    </div>

    <div v-if="showCreate" class="modal-mask" @click.self="showCreate = false">
      <form class="modal" @submit.prevent="createVessel">
        <h3>登记船舶</h3>
        <p class="page-desc">航线代码为必填，空着不许提交。</p>
        <label v-for="field in createFields" :key="field" class="form-item">
          <span>{{ field }}<em v-if="field === '航线代码'"> *</em></span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="showCreate = false">取消</button>
          <button class="btn primary" type="submit">提交登记</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>看板、明细、台账共用同一份作业条目数据</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
      <span v-else-if="successMessage" class="ok-text">{{ successMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

const ENDPOINT = '/api/vessel'

type ShipRow = {
  vessel_id: number
  船舶编号: string
  船名: string
  航线代码: string
  船舶状态: string
  预计作业箱量: string | number
  条目总数: number
  待开工: number
  作业中: number
  已核对: number
  明细箱量: number
  核对箱量: number
  可离泊: boolean
}
type WorkRow = Record<string, string | number | null> & { id: number; vessel_id: number; status: string }
type LedgerRow = Record<string, string | number | null>

type BatchResponse = {
  ok: boolean
  message: string
  duplicate?: boolean
  advanced: { id: number }[]
  skipped: unknown[]
  conflicted: unknown[]
  ledger: Record<string, unknown> | null
}

const workColumns = ['作业条目编号', '作业类型', '贝位号', '岸桥', '作业箱量', '理货核对']
const createFields = ['船舶编号', '船名', '船公司', '航线代码', '进口航次', '出口航次', '预计作业箱量']
const stages = ['待开工', '作业中', '已核对']

const board = ref<ShipRow[]>([])
const works = ref<WorkRow[]>([])
const ledger = ref<LedgerRow[]>([])
const currentVesselId = ref<number | null>(null)
const selectedIds = ref<number[]>([])
const errorMessage = ref('')
const successMessage = ref('')
const showCreate = ref(false)
const createForm = reactive<Record<string, string>>({})

const currentVessel = computed(() =>
  board.value.find((ship) => ship.vessel_id === currentVesselId.value) ?? null,
)
const stats = computed(() => [
  { label: '待开工条目', value: board.value.reduce((sum, s) => sum + s.待开工, 0) },
  { label: '作业中条目', value: board.value.reduce((sum, s) => sum + s.作业中, 0) },
  { label: '已核对条目', value: board.value.reduce((sum, s) => sum + s.已核对, 0) },
  { label: '可离泊船舶', value: board.value.filter((s) => s.可离泊).length },
])
const summary = computed(() => ({
  total: works.value.reduce((sum, w) => sum + Number(w['作业箱量'] ?? 0), 0),
  checked: works.value
    .filter((w) => w.status === '已核对')
    .reduce((sum, w) => sum + Number(w['作业箱量'] ?? 0), 0),
}))
const canDepart = computed(
  () =>
    currentVessel.value !== null &&
    currentVessel.value.船舶状态 !== '已离泊' &&
    works.value.length > 0 &&
    works.value.every((w) => w.status === '已核对'),
)
const allSelected = computed(
  () => works.value.length > 0 && selectedIds.value.length === works.value.length,
)
const someSelected = computed(
  () => selectedIds.value.length > 0 && !allSelected.value,
)

function stageIndex(status: string): number {
  const index = stages.indexOf(status)
  return index === -1 ? 0 : index
}

function statusRowClass(status: string): string {
  return { 待开工: 'row-todo', 作业中: 'row-doing', 已核对: 'row-done' }[status] ?? ''
}

function openCreate() {
  createFields.forEach((field) => {
    createForm[field] = ''
  })
  showCreate.value = true
}

async function createVessel() {
  errorMessage.value = ''
  try {
    const response = await request(ENDPOINT, {
      method: 'POST',
      body: JSON.stringify({ values: { ...createForm } }),
    })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message)
    }
    showCreate.value = false
    successMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '船舶登记失败'
  }
}

async function selectVessel(vesselId: number) {
  currentVesselId.value = vesselId
  selectedIds.value = []
  await loadWorks()
}

async function loadWorks() {
  if (currentVesselId.value === null) {
    works.value = []
    return
  }
  try {
    const response = await request(`${ENDPOINT}/${currentVesselId.value}/works`)
    if (!response.ok) {
      throw new Error('作业条目读取失败')
    }
    const payload = await response.json()
    works.value = payload.items ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '作业条目读取失败'
  }
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  if (currentVessel.value?.船舶状态 === '已离泊') {
    return
  }
  selectedIds.value = checked ? works.value.map((w) => w.id) : []
}

/** 幂等键随「船 + 动作 + 选中条目」生成：同一批重复提交后端只留一条。 */
function idempotencyKey(action: string): string {
  const ids = [...selectedIds.value].sort((a, b) => a - b).join('_')
  return `v${currentVesselId.value}-${action}-${ids}`
}

async function submitBatch(action: string) {
  if (currentVesselId.value === null || selectedIds.value.length === 0) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  const workIds = [...selectedIds.value]
  try {
    const response = await request(`${ENDPOINT}/works/batch`, {
      method: 'POST',
      body: JSON.stringify({
        work_ids: workIds,
        action,
        vessel_id: currentVesselId.value,
        idempotency_key: idempotencyKey(action),
      }),
    })
    const result = (await response.json()) as BatchResponse
    if (!response.ok || !result.ok) {
      throw new Error(result.message || '整组提交未生效')
    }
    successMessage.value = result.message
    selectedIds.value = []
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '整组提交失败'
    await reload()
  }
}

async function rejectOne(work: WorkRow) {
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/works/${work.id}/reject`, { method: 'POST' })
    const result = (await response.json()) as BatchResponse
    if (!response.ok || !result.ok) {
      throw new Error(result.message || '打回未生效')
    }
    successMessage.value = result.message
    selectedIds.value = selectedIds.value.filter((id) => id !== work.id)
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '打回失败'
  }
}

async function runShipAction(action: string) {
  if (currentVesselId.value === null) {
    return
  }
  errorMessage.value = ''
  successMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${currentVesselId.value}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const result = await response.json()
    if (!result.ok) {
      throw new Error(result.message)
    }
    successMessage.value = result.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '船舶操作失败'
  }
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function reload() {
  errorMessage.value = ''
  try {
    const [boardRes, ledgerRes] = await Promise.all([
      request(`${ENDPOINT}/board`),
      request(`${ENDPOINT}/ledger`),
    ])
    if (!boardRes.ok) {
      throw new Error('作业看板读取失败')
    }
    const boardPayload = await boardRes.json()
    board.value = boardPayload.ships ?? []
    const ledgerPayload = ledgerRes.ok ? await ledgerRes.json() : { items: [] }
    ledger.value = ledgerPayload.items ?? []
    if (currentVesselId.value !== null) {
      await loadWorks()
    }
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '船舶作业数据读取失败'
  }
}

onMounted(async () => {
  await reload()
  if (board.value.length > 0) {
    await selectVessel(board.value[0].vessel_id)
  }
})
</script>

<style scoped>
.board-wrap,
.work-wrap,
.ledger-wrap {
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  margin-bottom: 14px;
}
.board-wrap h3,
.work-wrap h3,
.ledger-wrap h3 {
  margin: 0 0 10px;
  font-size: 15px;
}
.board-wrap tr.active {
  background: #eef4ff;
}
.work-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
}
.ship-actions {
  display: flex;
  gap: 8px;
}
.batch-bar {
  display: flex;
  align-items: center;
  gap: 10px;
  margin: 10px 0;
  flex-wrap: wrap;
}
.check-all {
  font-size: 13px;
  display: flex;
  align-items: center;
  gap: 4px;
}
.btn:disabled,
.link:disabled {
  opacity: 0.45;
  cursor: not-allowed;
}
.btn.danger {
  border-color: #d92d20;
  color: #d92d20;
}
.tag {
  display: inline-block;
  padding: 1px 8px;
  border-radius: 10px;
  background: #e2e8f0;
  font-size: 12px;
}
.tag-todo {
  background: #fef0c7;
  color: #b54708;
}
.stage {
  display: inline-block;
  min-width: 52px;
  text-align: center;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 12px;
}
.stage-0 { background: #f2f4f7; color: #475467; }
.stage-1 { background: #dbeafe; color: #1d4ed8; }
.stage-2 { background: #dcfae6; color: #027a48; }
.row-todo { background: #fcfcfd; }
.row-doing { background: #f5f9ff; }
.row-done { background: #f6fef9; }
.ok-text { color: #027a48; font-weight: 600; }
.muted-text { color: var(--muted); font-size: 12px; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(15, 23, 42, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 20;
}
.modal {
  width: 420px;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal h3 {
  margin: 0 0 4px;
}
.form-item {
  display: block;
  margin-top: 10px;
}
.form-item span {
  display: block;
  font-size: 12px;
  color: var(--muted);
  margin-bottom: 3px;
}
.form-item em {
  color: #d92d20;
  font-style: normal;
}
.form-item input {
  width: 100%;
  padding: 6px 8px;
  border: 1px solid var(--border);
  border-radius: 6px;
}
.modal-actions {
  display: flex;
  justify-content: flex-end;
  gap: 8px;
  margin-top: 16px;
}
</style>
