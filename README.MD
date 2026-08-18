# PRIMER PARCIAL — FUNDAMENTOS DE INTELIGENCIA ARTIFICIAL
## Semestre 2026-2 — Universidad de la Sabana

[![Status](https://img.shields.io/badge/Status-En%20Desarrollo-orange.svg)](https://github.com/Dylan2099/PrimerParcial)
[![License](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

---

## OBJETIVO

Desarrollar un agente racional autónomo que planifique y ejecute operaciones críticas en una instalación experimental usando **Búsqueda de Costo Uniforme (UCS)**. El agente debe encontrar el plan de menor costo para restaurar las estaciones de la instalación, respetando las restricciones de batería, capacidad de carga y el contrato del entorno.

---

## ESTRUCTURA DEL PROYECTO

```
PrimerParcial/
├── README.MD              # Este archivo — instrucciones completas
├── CONTRATO.md            # Reglas oficiales del mundo
├── project/
│   ├── design.md          # Diseño del agente (obligatorio antes de implementar)
│   ├── README.md          # Referencia rápida del proyecto
│   ├── scenarios/
│   │   └── scenario.json  # Fuente de verdad de la misión
│   ├── backend/
│   │   ├── src/
│   │   │   ├── main.py
│   │   │   ├── demo_plan.py
│   │   │   └── simulator.py
│   │   ├── tests/
│   │   │   └── test_demo_plan.py
│   │   └── requirements.txt
│   └── frontend/
│       ├── src/
│       │   ├── App.tsx
│       │   ├── main.tsx
│       │   ├── index.css
│       │   ├── types.ts
│       │   ├── lib/
│       │   │   ├── api.ts
│       │   │   ├── executor.ts
│       │   │   └── grid.ts
│       │   ├── scene/
│       │   │   ├── Entities.tsx
│       │   │   ├── FloatingLabel.tsx
│       │   │   ├── Robot.tsx
│       │   │   └── World.tsx
│       │   ├── store/
│       │   │   └── simStore.ts
│       │   ├── ui/
│       │   │   └── HUD.tsx
│       │   └── public/
│       │       └── .gitkeep
│       ├── package.json
│       ├── vite.config.ts
│       ├── tsconfig.json
│       ├── tsconfig.node.json
│       └── tsconfig.root.json
```

---

## INSTRUCCIONES DE INSTALACIÓN

### 1. Instalar dependencias del backend

```powershell
# Desde la raíz del proyecto
cd PrimerParcial\project\backend

# Crear y activar entorno virtual Windows
python -m venv .venv
.\.venv\Scripts\activate

# Instalar dependencias
pip install -r requirements.txt
```

### 2. Instalar dependencias del frontend

```powershell
# Desde la raíz del proyecto
cd PrimerParcial\project\frontend

# Instalar dependencias de npm
npm install
```

---

## LEVANTAR EL SISTEMA

### Terminal 1 — Backend (API UCS)

```powershell
cd PrimerParcial\project\backend
.\.venv\Scripts\activate
uvicorn src.main:app --app-dir src --port 8000
```

**Verificar:** Abrir http://127.0.0.1:8000/api/health

### Terminal 2 — Frontend (Simulación visual)

```powershell
cd PrimerParcial\project\frontend
npm run dev
```

**Abrir:** http://localhost:5173

---

## EJECUTAR EL AGENTE

### Opción A: Usar endpoint `/api/solve`

```powershell
# Desde otro terminal o con curl
curl -X POST http://127.0.0.1:8000/api/solve \
  -H "Content-Type: application/json" \
  -d @PrimerParcial\project\scenarios\scenario.json
```

### Opción B: Usar el plan demo (para pruebas rápidas)

El backend incluye un plan artesanal de demostración que prueba la interfaz frontend sin necesidad de implementar la búsqueda UCS completa.

```powershell
cd PrimerParcial\project\backend
python tests\test_demo_plan.py
```

---

## PROBAR UNA MISIÓN

### Llamada al endpoint

La misión se envía como JSON POST a la API:

```json
{
  "meta": {
    "id": "emergency_control_demo",
    "title": "Emergency Control — Demo Mission",
    "description": "Restore GENERATOR, COMMAND and ARTILLARY stations. Resolvable by UCS."
  },
  "robot": {
    "start": "Z1",
    "battery_max": 100,
    "battery_start": 55,
    "cargo_capacity": 3
  },
  "zones": [...],
  "corridors": [...],
  "doors": [...],
  "keys": [...],
  "tools": [...],
  "materials": [...],
  "panels": [...],
  "stations": [...],
  "chargers": [...]
}
```

### Respuesta esperada

```json
{
  "success": true,
  "plan": [
    {"op": "MOVE", "from": "Z1", "to": "Z3"},
    {"op": "PICKUP", "obj": "KEY2"},
    {"op": "MOVE", "from": "Z3", "to": "Z2"},
    {"op": "PICKUP", "obj": "FUSE"},
    {"op": "MOVE", "from": "Z2", "to": "Z3"},
    {"op": "DROP", "obj": "KEY2"},
    {"op": "MOVE", "from": "Z3", "to": "Z4"},
    {"op": "INTERACT", "target": "PANEL_A", "action": "REPAIR", "consumes": "FUSE", "cost": 2},
    {"op": "MOVE", "from": "Z4", "to": "Z5"},
    {"op": "INTERACT", "target": "COMMAND", "action": "ACTIVATE", "cost": 1},
    {"op": "MOVE", "from": "Z5", "to": "Z4"},
    {"op": "INTERACT", "target": "ARTILLERY", "action": "ACTIVATE", "cost": 1},
    {"op": "MOVE", "from": "Z4", "to": "Z3"}
  ],
  "total_cost": 28,
  "metadata": {
    "nodes_expanded": 47,
    "max_frontier_size": 12,
    "solution_found": true
  }
}
```

### Significado de las operaciones visuales

El frontend reconoce exclusivamente estas 4 operaciones:

| Operación | Significado interno |
|---|---|
| `MOVE` | Mover robot de una zona a otra adyacente |
| `PICKUP` | Recoger un objeto del suelo |
| `DROP` | Dejar un objeto en la zona actual |
| `INTERACT` | Interactuar con un elemento (puerta, panel, estación, cargador) |

Dentro de `INTERACT`, el `action` especifica qué operación real se ejecuta:

```json
{"op": "INTERACT", "target": "PANEL_A", "action": "REPAIR", "consumes": "FUSE", "cost": 2}
```

Acciones `INTERACT` permitidas (según `CONTRATO.md`):

- `OPEN_DOOR` — abrir una puerta (requiere llave en payload)
- `REPAIR` — reparar un panel (requiere herramienta + material)
- `ACTIVATE` — activar una estación (requiere dependencias satisfechas)
- `RECHARGE` — recargar batería (robot en cargador)

---

## EJECUTAR LAS PRUEBAS

```powershell
# Desde el directorio del backend
cd PrimerParcial\project\backend
.\.venv\Scripts\activate
python tests\test_demo_plan.py
```

Las pruebas validan:

1. **Caso 1:** Estados equivalentes producen el mismo estado lógico
2. **Caso 2:** Configuraciones con información diferente se mantienen como estados distintos
3. **Caso 3:** Existe una instancia donde menor número de acciones ≠ menor costo
4. **Caso 4:** Agente retorna `FAILURE` cuando la misión es imposible
5. **Caso 5:** Múltiples rutas pueden alcanzar las mismas condiciones del mundo

---

## INTERPRETAR RESULTADOS

### Éxito (`success: true`)

- **plan:** Secuencia de acciones para completar la misión
- **total_cost:** Costo acumulado del plan (debe ser mínimo)
- **metadata:** Información de diagnóstico (nodos expandidos, tamaño de frontera)

### Fallo (`success: false`)

- **error:** Descripción del problema
- **El agente debe retornar `FAILURE`** cuando la misión no pueda completarse (no quedar atrapado indefinidamente)

### Métricas de rendimiento

- **nodes_expanded:** Número de estados expandidos durante la búsqueda
- **max_frontier_size:** Tamaño máximo de la frontera en memoria
- **solution quality:** Ratio de costo encontrado vs costo óptimo teórico

---

## Solicitudes Especiales

### No modificar `scenario.json`

**Nunca** suba `cargo_capacity`, `battery_max` ni cambie la configuración del escenario para hacer que UCS termine. Si la búsqueda no termina, **formule `Applicable` mejor** (ver `design.md` §2.2 y §4.6).

### Diseño del agente vs contrato visual

- **Agente (estudiante):** Modela acciones internas (`REPAIR_PANEL_A`, `ACTIVATE_STATION`, etc.)
- **Frontend / banco de pruebas:** Solo ejecuta `MOVE`/`PICKUP`/`DROP`/`INTERACT`

El plan retornado por `/api/solve` debe traducirse al contrato cerrado: las 4 operaciones visuales y, dentro de `INTERACT`, únicamente las acciones enumeradas en `CONTRATO.md`.

### Batería y capacidad

- La batería es parte del estado físico; dos configuraciones idénticas con diferente batería son situaciones diferentes
- La capacidad de carga 3 es intencional: obliga a `DROP` reales para liberar espacio
- No use la batería como excusa para no implementar la poda adecuada

---

## 📝 ENTREGABLES

1. **`design.md`** — Diseño formal del agente (estado, acciones, transición, meta, costo, búsqueda)
2. **Backend implementado** — `POST /api/solve` con búsqueda UCS
3. **Pruebas** — Que validen las 5 propiedades requeridas (Casos 1-5)
4. **README.MD** — Instrucciones completas (este archivo)
5. **Integración visual** — El frontend muestra posición, batería, progreso, acciones ejecutadas y costo acumulado

---

## CONSEJOS PARA EL ÉXITO

1. **Empiece por `design.md`:** Complete todas las subsecciones antes de implementar
2. **Cuidado con `DROP`:** Restrinja cuándo generar `DROP` para evitar explosión combinatoria del espacio de estados
3. **Use estados canónicos:** Representa materiales por tipo (`FUSE`, `CHIP`, `CABLE`), no por ids artificiales
4. **Aplique dominancia de batería:** Si dos caminos llegan al mismo mundo físico y uno tiene más batería a menor costo, el otro está dominado
5. **Elimine objetos muertos:** Objetos cuya ubicación no afecta acciones futuras pueden ignorarse
6. **Pruebe con el plan demo primero:** Asegúrese de que el frontend funcione antes de implementar la búsqueda completa
7. **Justifique decisiones:** Durante la revisión, deberá explicar cualquier decisión importante del modelo del agente

---

## 📞 SOPORTE

- Revisar `CONTRATO.md` para reglas oficiales del mundo
- Revisar `project/design.md` para el diseño del agente
- Ejecutar `python tests/test_demo_plan.py` para validaciones básicas
- El profesor probará con instancias adicionales que cambien posiciones, costos, recursos y configuración del entorno

---
*Última actualización: 2026-08-18*