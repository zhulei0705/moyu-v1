// Console boundary validation. Missing values are never converted to zero.
export function numberValue(value: unknown): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value >= 0 ? value : null
}
export function metric(value: unknown, digits?: number): string {
  if (value === null || value === undefined) return '待获取'
  const n = numberValue(value)
  return n === null ? '暂无有效数据' : digits === undefined ? String(n) : n.toFixed(digits)
}
export function bagFree(capacity: unknown, used: unknown): string {
  if (capacity == null || used == null) return '待获取'
  const c = numberValue(capacity), u = numberValue(used)
  return c === null || u === null || u > c ? '暂无有效数据' : metric(c - u)
}
export function ratio(numerator: unknown, denominator: unknown, scale = 1): number | null {
  const n = numberValue(numerator), d = numberValue(denominator)
  return n === null || d === null || d === 0 ? null : numberValue(n / d * scale)
}
export function calculated(value: unknown, digits = 1): string {
  const n = numberValue(value)
  return n === null ? '暂无有效数据' : n.toFixed(digits)
}
export function validStatus(value: any): boolean {
  return value != null && typeof value.running === 'boolean' && typeof value.paused === 'boolean'
    && typeof value.state === 'string' && typeof value.scene_id === 'string'
}
export class ApiError extends Error {
  code: string
  status?: number
  constructor(message: string, code: string, status?: number) { super(message); this.code=code; this.status=status }
}
export async function request(path: string, options: RequestInit = {}, timeout = 4000): Promise<any> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)
  try {
    const response = await fetch('/api/' + path, {...options, signal: controller.signal, cache: 'no-store'})
    if (!response.ok) {
      const body = await response.text()
      let detail = body
      try { const parsed = JSON.parse(body); detail = typeof parsed.detail === 'string' ? parsed.detail : '请求参数无效' } catch { /* plain HTTP error */ }
      throw new ApiError(`请求失败（HTTP ${response.status}）：${detail.slice(0, 240)}`, 'HTTP', response.status)
    }
    return await response.json()
  } catch (error) {
    if (error instanceof ApiError) throw error
    if (controller.signal.aborted) throw new ApiError('请求超时，请检查本地服务；控制结果尚未确认，请先重新同步状态。', 'TIMEOUT')
    if (error instanceof SyntaxError) throw new ApiError('服务返回的数据格式无效，请重新同步。', 'DATA')
    throw new ApiError('无法连接本地服务，请确认服务已启动且当前端口正确。连接恢复后会自动同步，后端任务不会因页面断线而停止。', 'NETWORK')
  } finally { clearTimeout(timer) }
}
