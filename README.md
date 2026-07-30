# QAPV Legacy App Analyzer

Automatiza el primer borrador del análisis de aplicaciones legacy de QAPV_DATACENTER
para el proyecto de migración a Ignition MES.

Reemplaza el flujo manual de "abrir dotPeek → decompilar clase por clase → copiar
a un .txt → analizar a mano" por:

1. **Decompilación** (`ilspycmd`, motor de ILSpy) — decompila el `.exe`/`.dll` completo
   como proyecto (reconstruye toda la estructura de carpetas, incluyendo View/ViewModel/Model
   en apps WPF/MVVM, y solo decompila el ensamblado que le indiques, sin arrastrar
   librerías de terceros referenciadas).
2. **Extracción** — escanea todo el código `.cs` generado y encuentra:
   - Connection strings (`Settings.Designer.cs` / `Settings.cs`)
   - Cada uso de `SqlConnection` / `OracleConnection`
   - El texto de la query o el nombre del Stored Procedure en cada `CommandText`
   - La función donde vive cada uno de esos usos
3. **Reporte** — genera un `.md` en `reports/<App>.md` con el mismo formato que
   se ha usado manualmente en el análisis de las demás apps (conexiones + tabla
   función → SQL → tabla).

## Requisitos

- .NET SDK (ya instalado, ver `dotnet --version`)
- `ilspycmd` instalado como global tool: `dotnet tool install -g ilspycmd`
- Python 3.9+

## Uso

```powershell
python main.py "\\ruta\a\LaApp.exe"
```

Esto genera:
- `decompiled/<LaApp>/` — código fuente completo decompilado (gitignored, es grande)
- `reports/<LaApp>.md` — el reporte de conexiones + queries

Puedes pasar `--name` si quieres forzar el nombre de salida:

```powershell
python main.py "\\ruta\a\LaApp.exe" --name "MiApp"
```

## Importante — esto es un primer borrador, no el análisis final

Este script cubre el ~80% del trabajo mecánico (encontrar dónde está cada query),
pero **no reemplaza la revisión final**. Casos que el script marca como
"revisar manualmente" o que simplemente no detecta bien:

- SQL armado con `string[] strArray = {...}; string.Concat(strArray)` — el script
  intenta reconstruirlo pero puede quedar incompleto.
- Análisis de lógica de negocio (p. ej. detectar que una app es código duplicado
  de otra, como pasó con CopyVIAVI/CopyJDSU) — eso requiere criterio, no regex.
- Confirmar cuál connection string realmente usa la app en producción (los
  `DefaultSettingValue` son solo el default; el `app.config` desplegado puede
  sobreescribirlo).

Usa el reporte generado como punto de partida y sigue completando/verificando
igual que hemos hecho manualmente para el resto de las apps.
