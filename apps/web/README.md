# nvr-web — interface do NVR

React + TypeScript (Vite). Fala só com a API do backend (`/api/v1`), por um cliente tipado gerado do OpenAPI.

```bash
npm ci
npm run dev        # http://localhost:5173 (encaminha /api para o backend em 127.0.0.1:8000)
npm test           # Vitest
npm run gen:api    # regenera src/api/schema.d.ts a partir do backend (requer uv)
```

Regras: nada de lógica de protocolo de câmera nem credenciais de câmera no frontend (proposta, seção 4).
