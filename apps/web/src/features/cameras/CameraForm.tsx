import { useState, type FormEvent } from 'react'
import { ApiError, type Camera, type CameraCreate } from '../../api/client'
import { useCreateCamera, useUpdateCamera } from './queries'

const XIONGMAI_MAIN = '/user={user}&password={password}&channel=1&stream=0.sdp?'
const XIONGMAI_SUB = '/user={user}&password={password}&channel=1&stream=1.sdp?'

type Props = { camera?: Camera; onDone: () => void }

type FormState = {
  name: string
  host: string
  rtsp_port: string
  main_path: string
  sub_path: string
  username: string
  password: string
  driver: 'generic_rtsp' | 'xiongmai'
  recording_enabled: boolean
}

function initialState(camera?: Camera): FormState {
  return {
    name: camera?.name ?? '',
    host: camera?.host ?? '',
    rtsp_port: String(camera?.rtsp_port ?? 554),
    main_path: camera?.main_path ?? '',
    sub_path: camera?.sub_path ?? '',
    username: '',
    password: '',
    driver: camera?.driver ?? 'generic_rtsp',
    recording_enabled: camera?.recording_enabled ?? true,
  }
}

export function CameraForm({ camera, onDone }: Props) {
  const editing = camera !== undefined
  const [form, setForm] = useState<FormState>(() => initialState(camera))
  const create = useCreateCamera()
  const update = useUpdateCamera()
  const mutation = editing ? update : create
  const fieldErrors = mutation.error instanceof ApiError ? mutation.error.fields : {}

  function set<K extends keyof FormState>(key: K, value: FormState[K]) {
    setForm((f) => ({ ...f, [key]: value }))
  }

  function useXiongmaiPreset() {
    setForm((f) => ({ ...f, driver: 'xiongmai', main_path: XIONGMAI_MAIN, sub_path: XIONGMAI_SUB }))
  }

  function submit(event: FormEvent) {
    event.preventDefault()
    const common = {
      name: form.name,
      host: form.host,
      rtsp_port: Number(form.rtsp_port),
      main_path: form.main_path,
      sub_path: form.sub_path,
      driver: form.driver,
      recording_enabled: form.recording_enabled,
    }
    if (editing) {
      // Credenciais só são trocadas se as duas forem preenchidas.
      const credentials = form.username && form.password ? { username: form.username, password: form.password } : {}
      update.mutate({ id: camera.id, body: { ...common, ...credentials } }, { onSuccess: onDone })
    } else {
      const body: CameraCreate = { ...common, sub_path: form.sub_path || null, username: form.username, password: form.password }
      create.mutate(body, { onSuccess: onDone })
    }
  }

  const err = (field: string) =>
    fieldErrors[field] ? (
      <span className="field-error" role="alert">
        {fieldErrors[field]}
      </span>
    ) : null

  return (
    <form className="card form" onSubmit={submit} aria-label={editing ? 'Editar câmera' : 'Nova câmera'}>
      <h2>{editing ? `Editar ${camera.name}` : 'Nova câmera'}</h2>

      <label>
        Nome
        <input value={form.name} onChange={(e) => set('name', e.target.value)} required maxLength={80} />
        {err('name')}
      </label>

      <div className="form-row">
        <label className="grow">
          IP ou host
          <input value={form.host} onChange={(e) => set('host', e.target.value)} required placeholder="10.0.0.210" />
          {err('host')}
        </label>
        <label className="narrow">
          Porta RTSP
          <input
            type="number"
            min={1}
            max={65535}
            value={form.rtsp_port}
            onChange={(e) => set('rtsp_port', e.target.value)}
            required
          />
          {err('rtsp_port')}
        </label>
      </div>

      <div className="form-hint">
        <span>Câmera iCSee/XMEye (Xiongmai)?</span>
        <button type="button" className="secondary" onClick={useXiongmaiPreset}>
          Usar caminhos iCSee
        </button>
      </div>

      <label>
        Caminho do stream principal
        <input
          value={form.main_path}
          onChange={(e) => set('main_path', e.target.value)}
          required
          placeholder="/stream1"
          spellCheck={false}
        />
        <small className="muted">
          Se a câmera exige usuário/senha no caminho, use <code>{'{user}'}</code> e <code>{'{password}'}</code>.
        </small>
        {err('main_path')}
      </label>

      <label>
        Caminho do substream (opcional)
        <input value={form.sub_path} onChange={(e) => set('sub_path', e.target.value)} spellCheck={false} />
        {err('sub_path')}
      </label>

      <div className="form-row">
        <label className="grow">
          Usuário da câmera
          <input
            value={form.username}
            onChange={(e) => set('username', e.target.value)}
            required={!editing}
            autoComplete="off"
            placeholder={editing ? '(manter o atual)' : ''}
          />
        </label>
        <label className="grow">
          Senha da câmera
          <input
            type="password"
            value={form.password}
            onChange={(e) => set('password', e.target.value)}
            required={!editing}
            autoComplete="new-password"
            placeholder={editing ? '(manter a atual)' : ''}
          />
        </label>
      </div>
      {err('credentials')}

      <label className="checkbox">
        <input
          type="checkbox"
          checked={form.recording_enabled}
          onChange={(e) => set('recording_enabled', e.target.checked)}
        />
        Gravar continuamente
      </label>

      {mutation.isError && Object.keys(fieldErrors).length === 0 && (
        <p className="error" role="alert">
          {mutation.error.message}
        </p>
      )}

      <div className="form-actions">
        <button type="button" className="secondary" onClick={onDone}>
          Cancelar
        </button>
        <button type="submit" disabled={mutation.isPending}>
          {mutation.isPending ? 'Salvando…' : 'Salvar'}
        </button>
      </div>
    </form>
  )
}
