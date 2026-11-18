<template>
  <section class="page" data-module="retention_matrix">
    <header class="page-head">
      <div>
        <h2>留存矩阵数据表</h2>
        <p class="page-desc">读取同期群定义与成员事件，按首次行为周期计算留存率；矩阵经 V001 留档核对后发布，每列留存率可回溯到同期群定义。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" data-testid="open-compute" type="button" @click="openCompute">计算留存矩阵</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出留存矩阵</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="card in overviewCards" :key="card.label" class="stat-card" data-testid="kpi-card">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <div class="timeline-wrap" data-testid="retention-timeline">
      <h3 class="block-title">留存矩阵时间线</h3>
      <div v-for="item in timelineItems" :key="item.id" class="timeline-row">
        <span class="timeline-label">{{ item.首次行为周期 }} · {{ item.群组编号 }}</span>
        <div class="timeline-track">
          <div
            class="timeline-bar"
            :class="item.status === '已停用' ? 'bar-deactivated' : 'bar-confirmed'"
            :style="{ width: barWidth(item) }"
            :title="`${item.矩阵编号} 第1期留存率 ${fmtRate(item['第1期'])}`"
          >{{ item.矩阵编号 }} · {{ item.status }}</div>
        </div>
      </div>
      <p v-if="!timelineItems.length" class="empty-state">暂无已确认矩阵，先计算并通过 V001 核对</p>
    </div>

    <template v-if="drafts.length">
      <h3 class="block-title">待 V001 核对草稿</h3>
      <table class="data-table" data-testid="draft-table">
        <thead>
          <tr><th v-for="c in draftColumns" :key="c">{{ c }}</th><th>可执行动作</th></tr>
        </thead>
        <tbody>
          <tr v-for="row in drafts" :key="`draft-${row.id}`" :data-testid="`draft-row-${row.id}`">
            <td v-for="c in draftColumns" :key="c">{{ c in periodSet ? fmtRate(row[c]) : (row[c] ?? '—') }}</td>
            <td class="row-actions">
              <button class="link" type="button" :data-testid="`publish-${row.id}`" @click="publish(row)">V001核对发布</button>
            </td>
          </tr>
        </tbody>
      </table>
    </template>

    <h3 class="block-title">已确认留存矩阵</h3>
    <table class="data-table" data-testid="matrix-table">
      <thead>
        <tr><th v-for="c in columns" :key="c">{{ c }}</th><th>可执行动作</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
          <td v-for="c in columns" :key="c">{{ c in periodSet ? fmtRate(row[c]) : (row[c] ?? '—') }}</td>
          <td class="row-actions">
            <button class="link" type="button" :data-testid="`detail-${row.id}`" @click="openDetail(row)">详情</button>
            <button class="link" type="button" :data-testid="`trace-${row.id}`" @click="openTrace(row)">列回溯</button>
            <button v-if="row.status === '已确认'" class="link" type="button" :data-testid="`deactivate-${row.id}`" @click="openDeactivate(row)">停用群组</button>
            <button class="link" type="button" :data-testid="`retry-${row.id}`" @click="retry(row)">重试补写</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无已确认矩阵，列表只读取 V001 核对通过的确认结果</td>
        </tr>
      </tbody>
    </table>

    <div v-if="showCompute" class="modal-backdrop" data-testid="compute-modal">
      <form class="modal" @submit.prevent="submitCompute">
        <h3>计算留存矩阵</h3>
        <label v-for="field in computeFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <select v-if="field === '周期口径'" v-model="computeValues[field]" :data-testid="`compute-${field}`">
            <option v-for="o in caliberOptions" :key="o" :value="o">{{ o }}</option>
          </select>
          <input v-else v-model="computeValues[field]" :data-testid="`compute-${field}`" required />
        </label>
        <p class="hint">留存周期必须与成员事件口径同时登记，口径不一致的周期不会计入矩阵。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showCompute = false">取消</button>
          <button class="btn primary" data-testid="submit-compute" type="submit" :disabled="computing">{{ computing ? '计算中…' : '确认计算' }}</button>
        </div>
      </form>
    </div>

    <div v-if="detail" class="modal-backdrop" data-testid="detail-modal">
      <div class="modal">
        <h3>留存矩阵详情 · {{ detail.矩阵编号 }}</h3>
        <dl class="detail-grid">
          <template v-for="key in detailFields" :key="key">
            <dt>{{ key }}</dt><dd>{{ key in periodSet ? fmtRate(detail[key]) : (detail[key] ?? '—') }}</dd>
          </template>
        </dl>
        <table class="data-table">
          <thead><tr><th>周期</th><th>留存人数</th><th>分母样本</th><th>留存率</th><th>回溯群组</th></tr></thead>
          <tbody>
            <tr v-for="d in detail.留存明细 ?? []" :key="d.周期">
              <td>{{ d.周期 }}</td><td>{{ d.留存人数 }}</td><td>{{ d.分母样本 }}</td><td>{{ fmtRate(d.留存率) }}</td><td>{{ d.群组编号 }}</td>
            </tr>
          </tbody>
        </table>
        <div class="page-actions"><button class="btn" type="button" @click="detail = null">关闭</button></div>
      </div>
    </div>

    <div v-if="traceTarget" class="modal-backdrop" data-testid="trace-modal">
      <div class="modal">
        <h3>列回溯 · {{ traceTarget.矩阵编号 }}</h3>
        <div class="filter-bar">
          <label class="filter-item"><span>周期列</span>
            <select v-model="tracePeriod" data-testid="trace-period">
              <option v-for="p in periods" :key="p" :value="p">{{ p }}</option>
            </select>
          </label>
          <button class="btn" type="button" data-testid="run-trace" @click="runTrace">回溯</button>
        </div>
        <template v-if="traceResult">
          <dl class="detail-grid">
            <dt>周期</dt><dd>{{ traceResult.周期 }}</dd>
            <dt>留存率</dt><dd>{{ fmtRate(traceResult.留存率) }}</dd>
            <dt>留存人数</dt><dd>{{ traceResult.留存人数 }}</dd>
            <dt>分母样本</dt><dd>{{ traceResult.分母样本 }}</dd>
            <dt>核对留档</dt><dd>{{ traceResult.核对留档 ?? '—' }}</dd>
            <dt>群组编号</dt><dd>{{ traceResult.群组编号 }}</dd>
          </dl>
          <h4>同期群定义</h4>
          <dl class="detail-grid">
            <template v-for="key in cohortFields" :key="key">
              <dt>{{ key }}</dt><dd>{{ traceResult.同期群定义?.[key] ?? '—' }}</dd>
            </template>
          </dl>
          <h4>分母成员样本</h4>
          <p class="hint">{{ (traceResult.成员样本 ?? []).join('、') || '—' }}</p>
        </template>
        <div class="page-actions"><button class="btn" type="button" @click="traceTarget = null; traceResult = null">关闭</button></div>
      </div>
    </div>

    <div v-if="deactivateTarget" class="modal-backdrop" data-testid="deactivate-modal">
      <form class="modal" @submit.prevent="submitDeactivate">
        <h3>停用群组 · {{ deactivateTarget.群组编号 }}</h3>
        <label class="create-field"><span>停用时点</span>
          <input v-model="deactivateValues.停用时点" data-testid="deactivate-time" type="date" required />
        </label>
        <label class="create-field"><span>停用原因</span>
          <input v-model="deactivateValues.停用原因" data-testid="deactivate-reason" placeholder="请填写停用原因" required />
        </label>
        <p class="hint">停用后已发布矩阵保留完整历史周期并附停用原因；与新周期重算冲突时保留停用前口径并延后新周期。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="deactivateTarget = null">取消</button>
          <button class="btn primary" data-testid="submit-deactivate" type="submit">确认停用</button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条已确认留存矩阵</span>
      <span v-if="message" class="hint">{{ message }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, any>

const ENDPOINT = '/api/retention_matrix'
const periods = ['第0期', '第1期', '第2期', '第3期']
const periodSet = Object.fromEntries(periods.map((p) => [p, true]))
const columns = ['矩阵编号', '群组编号', '指标编号', '周期口径', '首次行为周期', '分母样本', ...periods, 'status', '核对留档', '停用原因']
const draftColumns = ['矩阵编号', '群组编号', '指标编号', '周期口径', '首次行为周期', '分母样本', ...periods]
const detailFields = ['矩阵编号', '群组编号', '指标编号', '周期口径', '首次行为周期', '分母样本', ...periods, 'status', '核对留档', '停用时点', '停用原因', '重算状态']
const cohortFields = ['群组编号', '群组名称', '首次行为', '时间基准', '追踪周期', '留存窗口', '分析指标']
const computeFields = ['群组编号', '指标编号', '周期口径']
const caliberOptions = ['按周', '按日', '按月']

const rows = ref<Row[]>([])
const drafts = ref<Row[]>([])
const timelineItems = ref<Row[]>([])
const overviewCards = ref<{ label: string; value: number }[]>([])
const total = ref(0)
const message = ref('')
const errorMessage = ref('')
const showCompute = ref(false)
const computing = ref(false)
const computeValues = ref<Record<string, string>>({ 周期口径: '按周' })
const detail = ref<Row | null>(null)
const traceTarget = ref<Row | null>(null)
const tracePeriod = ref('第1期')
const traceResult = ref<Row | null>(null)
const deactivateTarget = ref<Row | null>(null)
const deactivateValues = ref<Record<string, string>>({ 停用时点: '', 停用原因: '' })

function fmtRate(value: unknown) {
  if (value === null || value === undefined || value === '') return '—'
  const num = Number(value)
  return Number.isFinite(num) ? `${(num * 100).toFixed(1)}%` : String(value)
}

function barWidth(item: Row) {
  const rate = Number(item['第1期'] ?? 0)
  return `${Math.max(8, Math.min(100, rate * 100))}%`
}

async function readPayload(res: Response) {
  const payload = await res.json().catch(() => ({}))
  if (!res.ok) throw new Error(payload.detail || payload.message || `接口返回 ${res.status}`)
  return payload
}

async function reload() {
  errorMessage.value = ''
  try {
    const [listRes, draftRes, timelineRes, overviewRes] = await Promise.all([
      request(`${ENDPOINT}?size=200`),
      request(`${ENDPOINT}/drafts`),
      request(`${ENDPOINT}/timeline`),
      request(`${ENDPOINT}/overview`),
    ])
    const list = await readPayload(listRes)
    rows.value = list.items ?? []
    total.value = list.total ?? rows.value.length
    drafts.value = (await readPayload(draftRes)).items ?? []
    timelineItems.value = (await readPayload(timelineRes)).items ?? []
    overviewCards.value = (await readPayload(overviewRes)).cards ?? []
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '留存矩阵读取失败'
  }
}

function openCompute() {
  computeValues.value = { 群组编号: '', 指标编号: '', 周期口径: '按周' }
  message.value = ''
  errorMessage.value = ''
  showCompute.value = true
}

async function submitCompute() {
  computing.value = true
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/compute`, { method: 'POST', body: JSON.stringify({ values: computeValues.value }) })
    const payload = await readPayload(res)
    if (!payload.ok) throw new Error(payload.message || '计算失败')
    message.value = payload.message
    showCompute.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '计算失败'
  } finally {
    computing.value = false
  }
}

async function publish(row: Row) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${row.id}/publish`, { method: 'POST', body: JSON.stringify({}) })
    const payload = await readPayload(res)
    if (!payload.ok) throw new Error(payload.message || 'V001 核对未通过')
    message.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : 'V001 核对失败'
    await reload()
  }
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${row.id}`)
    detail.value = await readPayload(res)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '详情读取失败'
  }
}

function openTrace(row: Row) {
  traceTarget.value = row
  traceResult.value = null
  tracePeriod.value = '第1期'
}

async function runTrace() {
  if (!traceTarget.value) return
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${traceTarget.value.id}/trace/${encodeURIComponent(tracePeriod.value)}`)
    traceResult.value = await readPayload(res)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '列回溯失败'
  }
}

function openDeactivate(row: Row) {
  deactivateTarget.value = row
  deactivateValues.value = { 停用时点: new Date().toISOString().slice(0, 10), 停用原因: '' }
}

async function submitDeactivate() {
  if (!deactivateTarget.value) return
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${deactivateTarget.value.id}/deactivate`, {
      method: 'POST', body: JSON.stringify({ values: deactivateValues.value }),
    })
    const payload = await readPayload(res)
    if (!payload.ok) throw new Error(payload.message || '停用失败')
    message.value = payload.message
    deactivateTarget.value = null
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '停用失败'
  }
}

async function retry(row: Row) {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/${row.id}/retry`, { method: 'POST', body: JSON.stringify({}) })
    const payload = await readPayload(res)
    if (!payload.ok) throw new Error(payload.message || '重试失败')
    message.value = payload.message
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '重试失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  try {
    const res = await request(`${ENDPOINT}/export`)
    if (!res.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await res.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'retention_matrix.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(reload)
</script>

<style scoped>
.block-title { font-size: 14px; margin: 14px 0 8px; }
.timeline-wrap { margin-bottom: 4px; }
.timeline-bar.bar-confirmed { background: #22c55e; }
.timeline-bar.bar-deactivated { background: #94a3b8; }
.detail-grid { display: grid; grid-template-columns: 110px 1fr; gap: 6px 12px; margin: 0 0 12px; }
.detail-grid dt { color: var(--muted); font-size: 12px; }
.detail-grid dd { margin: 0; font-size: 13px; }
.hint { color: var(--muted); font-size: 12px; }
h4 { margin: 10px 0 6px; font-size: 13px; }
select { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 13px; }
</style>
