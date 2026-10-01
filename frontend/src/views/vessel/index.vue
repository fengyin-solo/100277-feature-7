<template>
  <section class="page" data-module="vessel">
    <header class="page-head">
      <div>
        <h2>船舶作业管理</h2>
        <p class="page-desc">
          船舶与作业条目共用一条单向作业链：条目按 待开工 → 作业中 → 已核对 推进，
          核对完才能确认离泊；同船条目可多选整组提交，箱量看板与作业明细同源。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记船舶</button>
        <button class="btn" type="button" @click="exportRows">导出船舶作业清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">总作业箱量（条目同源汇总）</span>
        <strong class="stat-value">{{ board['总作业箱量'] ?? 0 }}</strong>
      </article>
      <article v-for="status in itemStatuses" :key="status" class="stat-card">
        <span class="stat-label">{{ status }}条目</span>
        <strong class="stat-value">{{ board['条目状态分布']?.[status] ?? 0 }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待核对（不含已离泊）</span>
        <strong class="stat-value">{{ board['待核对条目数'] ?? 0 }}</strong>
      </article>
      <article v-for="status in vesselStatuses" :key="status" class="stat-card">
        <span class="stat-label">{{ status }}</span>
        <strong class="stat-value">{{ board['船舶状态分布']?.[status] ?? 0 }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload()">
      <label class="filter-item">
        <span>船舶编号</span>
        <input v-model="keyword" placeholder="按船舶编号检索" />
      </label>
      <label class="filter-item">
        <span>船舶状态</span>
        <select v-model="status">
          <option value="">全部</option>
          <option v-for="s in vesselStatuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <h3 class="block-title">船舶作业台账</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>船舶编号</th>
          <th>船名</th>
          <th>船公司</th>
          <th>航线代码</th>
          <th>进口/出口航次</th>
          <th>作业箱量（同源）</th>
          <th>条目核对进度</th>
          <th>船舶状态</th>
          <th>台账待办</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="row in rows"
          :key="String(row.id)"
          :class="{ 'row-selected': selectedVesselId === row.id }"
        >
          <td><button class="link" type="button" @click="selectVessel(row.id)">{{ row['船舶编号'] }}</button></td>
          <td>{{ row['船名'] ?? '—' }}</td>
          <td>{{ row['船公司'] ?? '—' }}</td>
          <td>{{ row['航线代码'] || '—' }}</td>
          <td>{{ row['进口航次'] || '—' }} / {{ row['出口航次'] || '—' }}</td>
          <td>{{ row['作业箱量合计'] ?? 0 }}</td>
          <td>{{ row['已核对条目数'] ?? 0 }} / {{ row['作业条目数'] ?? 0 }}</td>
          <td>{{ row.status }}</td>
          <td>{{ row['待办数'] ?? 0 }}</td>
          <td class="row-actions">
            <button
              v-for="action in availableVesselAction(row.status)"
              :key="action"
              class="link"
              type="button"
              @click="runVesselAction(action, row)"
            >
              {{ action }}
            </button>
            <button class="link" type="button" @click="selectVessel(row.id)">查看条目</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="10" class="empty-state">暂无船舶作业数据，可先登记船舶</td>
        </tr>
      </tbody>
    </table>

    <section v-if="selectedVessel" class="item-panel">
      <header class="item-head">
        <h3 class="block-title">
          作业条目 · {{ selectedVessel['船舶编号'] }} {{ selectedVessel['船名'] }}
          <span class="item-meta">
            （{{ selectedVessel.status }}，条目箱量合计 {{ selectedVessel['作业箱量合计'] ?? 0 }}）
          </span>
        </h3>
        <div class="batch-bar">
          <label v-if="selectedItems.length" class="batch-count">
            已选 {{ selectedItems.length }} 条（箱量 {{ selectedBoxQty }}）
          </label>
          <button
            class="btn primary"
            type="button"
            :disabled="!canBatch('开工')"
            title="把勾选的待开工条目整组推进到作业中"
            @click="submitBatch('开工')"
          >
            整组开工
          </button>
          <button
            class="btn primary"
            type="button"
            :disabled="!canBatch('核对')"
            title="把勾选的作业中条目整组推进到已核对"
            @click="submitBatch('核对')"
          >
            整组核对
          </button>
        </div>
      </header>

      <table class="data-table">
        <thead>
          <tr>
            <th class="col-check"><input type="checkbox" :checked="allSelected" @change="toggleAll" /></th>
            <th>条目</th>
            <th>作业环节</th>
            <th>作业箱量</th>
            <th>作业链状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="item in itemRows" :key="String(item.id)" :class="{ 'row-abnormal': item.abnormal }">
            <td>
              <input
                type="checkbox"
                :value="item.id"
                v-model="checkedIds"
                :disabled="!isSelectable(item)"
              />
            </td>
            <td>#{{ item.id }}</td>
            <td>{{ item['作业环节'] }}</td>
            <td>{{ item['作业箱量'] }}</td>
            <td>
              <span class="chain">
                <template v-for="(stage, idx) in itemStatuses" :key="stage">
                  <span :class="['chain-node', stageClass(item.status, stage)]">{{ stage }}</span>
                  <span v-if="idx < itemStatuses.length - 1" class="chain-arrow">→</span>
                </template>
              </span>
            </td>
            <td class="row-actions">
              <button
                v-if="canReject"
                class="link danger"
                type="button"
                @click="rejectItem(item)"
              >
                打回
              </button>
              <span v-else class="muted-text">—</span>
            </td>
          </tr>
          <tr v-if="!itemRows.length">
            <td colspan="6" class="empty-state">这条船还没有作业条目</td>
          </tr>
        </tbody>
      </table>
      <p v-if="selectedVessel.status === '已离泊'" class="hint-text">
        船舶已确认离泊，作业条目已锁定，不能再提交或打回。
      </p>
      <p v-else-if="selectedVessel.status !== '作业中'" class="hint-text">
        船舶当前为「{{ selectedVessel.status }}」，开始作业后才能整组推进作业条目。
      </p>
    </section>

    <h3 class="block-title">船舶作业台账待办（确认离泊回写）</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>船舶编号</th>
          <th>船名</th>
          <th>航线代码</th>
          <th>事项</th>
          <th>条目数</th>
          <th>箱量合计</th>
          <th>状态</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="todo in todoRows" :key="String(todo.id)">
          <td>{{ todo['船舶编号'] }}</td>
          <td>{{ todo['船名'] }}</td>
          <td>{{ todo['航线代码'] }}</td>
          <td>{{ todo['事项'] }}</td>
          <td>{{ todo['作业条目数'] }}</td>
          <td>{{ todo['作业箱量合计'] }}</td>
          <td>{{ todo.status }}</td>
        </tr>
        <tr v-if="!todoRows.length">
          <td colspan="7" class="empty-state">暂无台账待办，确认离泊后会自动回写</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条船舶作业记录</span>
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>

    <dialog v-if="createOpen" class="create-dialog" open>
      <form method="dialog" class="create-form" @submit.prevent="submitCreate">
        <h3>登记船舶</h3>
        <p class="hint-text">航线代码是必填项，留空不允许提交。</p>
        <label v-for="field in createFields" :key="field" class="filter-item">
          <span>{{ field }}<em v-if="field === '航线代码'">（必填）</em></span>
          <input v-model="createForm[field]" :placeholder="`请输入${field}`" />
        </label>
        <div class="dialog-actions">
          <button type="button" class="btn ghost" @click="createOpen = false">取消</button>
          <button type="submit" class="btn primary">提交登记</button>
        </div>
      </form>
    </dialog>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/vessel'
const vesselStatuses = ['锚地待泊', '靠泊中', '作业中', '已离泊']
const itemStatuses = ['待开工', '作业中', '已核对']

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const status = ref('')

const board = ref<Record<string, any>>({})
const todoRows = ref<Row[]>([])

const selectedVesselId = ref<number | null>(null)
const itemRows = ref<Row[]>([])
const checkedIds = ref<number[]>([])
const submitting = ref(false)

const message = ref('')
const messageOk = ref(true)

const createOpen = ref(false)
const createFields = ['船舶编号', '船名', '船公司', '航线代码', '进口航次', '出口航次']
const createForm = ref<Record<string, string>>({})

const selectedVessel = computed<Row | null>(
  () => rows.value.find((row) => Number(row.id) === selectedVesselId.value) ?? null,
)
const selectedItems = computed(() =>
  itemRows.value.filter((item) => checkedIds.value.includes(Number(item.id))),
)
const selectedBoxQty = computed(() =>
  selectedItems.value.reduce((sum, item) => sum + Number(item['作业箱量'] ?? 0), 0),
)
const allSelected = computed(
  () => itemRows.value.length > 0 && itemRows.value.every((item) =>
    isSelectable(item) && checkedIds.value.includes(Number(item.id))),
)
const canReject = computed(() => selectedVessel.value?.status !== '已离泊')

function setMessage(text: string, ok = false) {
  message.value = text
  messageOk.value = ok
}

function availableVesselAction(current: string | number): string[] {
  const idx = vesselStatuses.indexOf(String(current))
  if (idx < 0 || idx >= vesselStatuses.length - 1) return []
  return idx === 2 ? ['确认离泊'] : [idx === 0 ? '安排靠泊' : '开始作业']
}

function isSelectable(item: Row): boolean {
  // 已核对和已离泊船上的条目不参与整组勾选
  return item.status !== '已核对' && selectedVessel.value?.status !== '已离泊'
}

function stageClass(current: string | number, stage: string): string {
  const ci = itemStatuses.indexOf(String(current))
  const si = itemStatuses.indexOf(stage)
  if (si < ci) return 'done'
  if (si === ci) return 'current'
  return 'todo'
}

function canBatch(action: '开工' | '核对'): boolean {
  if (submitting.value || !selectedItems.value.length) return false
  if (selectedVessel.value?.status !== '作业中') return false
  const need = action === '开工' ? '待开工' : '作业中'
  return selectedItems.value.every((item) => item.status === need)
}

function toggleAll(event: Event) {
  const checked = (event.target as HTMLInputElement).checked
  checkedIds.value = checked
    ? itemRows.value.filter(isSelectable).map((item) => Number(item.id))
    : []
}

function resetFilters() {
  keyword.value = ''
  status.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  createForm.value = Object.fromEntries(createFields.map((field) => [field, '']))
  createOpen.value = true
  setMessage('')
}

async function submitCreate() {
  setMessage('')
  const response = await request(ENDPOINT, {
    method: 'POST',
    body: JSON.stringify({ values: createForm.value }),
  })
  const payload = await response.json()
  if (!response.ok || !payload.ok) {
    setMessage(payload.detail ?? payload.message ?? '船舶登记未生效')
    return
  }
  createOpen.value = false
  setMessage(payload.message ?? '船舶已登记', true)
  await reload()
}

async function runVesselAction(action: string, row: Row) {
  setMessage('')
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      setMessage(payload.detail ?? payload.message ?? '船舶作业动作未生效')
      return
    }
    setMessage(payload.message, true)
    await reload()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '船舶作业操作失败')
  }
}

async function submitBatch(action: '开工' | '核对') {
  if (!selectedVesselId.value || !checkedIds.value.length || submitting.value) return
  submitting.value = true
  setMessage('')
  try {
    // 每次整组提交生成一个批次号；重复提交（如双击重发）服务端只保留一条。
    const batchNo = `${action}-${selectedVesselId.value}-${[...checkedIds.value].sort().join('_')}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
    const response = await request(`${ENDPOINT}/${selectedVesselId.value}/items/actions`, {
      method: 'POST',
      body: JSON.stringify({ action, item_ids: checkedIds.value, batch_no: batchNo }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      setMessage(payload.detail ?? payload.message ?? '整组提交未生效')
      return
    }
    setMessage(payload.message, true)
    checkedIds.value = []
    await reload()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '整组提交失败')
  } finally {
    submitting.value = false
  }
}

async function rejectItem(item: Row) {
  setMessage('')
  try {
    const response = await request(`${ENDPOINT}/items/${item.id}/reject`, { method: 'POST' })
    const payload = await response.json()
    if (!response.ok || !payload.ok) {
      setMessage(payload.detail ?? payload.message ?? '打回未生效')
      return
    }
    checkedIds.value = checkedIds.value.filter((id) => id !== Number(item.id))
    setMessage(payload.message, true)
    await reload()
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '打回操作失败')
  }
}

async function selectVessel(vesselId: number) {
  selectedVesselId.value = vesselId
  checkedIds.value = []
  setMessage('')
  await loadItems(vesselId)
}

async function loadItems(vesselId: number) {
  try {
    const response = await request(`${ENDPOINT}/${vesselId}/items`)
    const payload = await response.json()
    itemRows.value = response.ok ? (payload.items ?? []) : []
  } catch {
    itemRows.value = []
  }
}

async function reload() {
  setMessage('')
  const keep = selectedVesselId.value
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (status.value) query.set('status', status.value)
  query.set('size', '200')
  try {
    const [listRes, boardRes, todoRes] = await Promise.all([
      request(`${ENDPOINT}?${query.toString()}`),
      request(`${ENDPOINT}/board`),
      request(`${ENDPOINT}/todos`),
    ])
    if (!listRes.ok) throw new Error('船舶列表读取失败')
    const payload = await listRes.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    board.value = boardRes.ok ? await boardRes.json() : {}
    todoRows.value = todoRes.ok ? ((await todoRes.json()).items ?? []) : []

    if (keep !== null) {
      if (rows.value.some((row) => Number(row.id) === keep)) {
        selectedVesselId.value = keep
        await loadItems(keep)
      } else {
        selectedVesselId.value = null
        itemRows.value = []
        checkedIds.value = []
      }
    }
  } catch (error) {
    setMessage(error instanceof Error ? error.message : '船舶作业列表读取失败')
  }
}

onMounted(async () => {
  await reload()
  const working = rows.value.find((row) => row.status === '作业中')
  if (working) await selectVessel(Number(working.id))
})
</script>

<style scoped>
.block-title { font-size: 15px; margin: 18px 0 8px; }
.row-selected { background: #eef5ff; }
.row-abnormal td { background: #fff6f5; }
.item-panel { margin-top: 10px; border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; background: #fff; }
.item-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; flex-wrap: wrap; }
.item-meta { color: var(--muted); font-size: 12px; font-weight: normal; }
.batch-bar { display: flex; gap: 8px; align-items: center; }
.batch-count { font-size: 12px; color: var(--brand); }
.btn:disabled { opacity: .45; cursor: not-allowed; }
.col-check { width: 36px; text-align: center; }
.chain { white-space: nowrap; font-size: 12px; display: inline-flex; align-items: center; gap: 4px; }
.chain-node { padding: 1px 6px; border-radius: 10px; border: 1px solid var(--border); color: var(--muted); }
.chain-node.done { background: #e7f6ec; border-color: #9ed8b0; color: #1f7a3d; }
.chain-node.current { background: #e8f0fe; border-color: var(--brand); color: var(--brand); font-weight: 600; }
.chain-arrow { color: var(--muted); }
.danger { color: #b42318; }
.muted-text { color: var(--muted); font-size: 12px; }
.hint-text { color: var(--muted); font-size: 12px; margin: 8px 0 0; }
.ok-text { color: #1f7a3d; }
.create-dialog { border: 1px solid var(--border); border-radius: 10px; padding: 16px 20px; min-width: 360px; }
.create-form { display: flex; flex-direction: column; gap: 8px; }
.create-form em { color: #b42318; font-style: normal; }
.dialog-actions { display: flex; justify-content: flex-end; gap: 8px; margin-top: 6px; }
</style>
