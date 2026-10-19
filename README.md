# Calculadora de APV régimen B

Simulación del aporte mensual de APV-B que minimiza el Impuesto Único de Segunda
Categoría (IUSC) de un trabajador dependiente en Chile. Reglas tributarias 2026
(tramos IUSC en UTM y tope de 600 UF anuales). Es una herramienta de apoyo, no
asesoría tributaria.

## Cómo ejecutar

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Lectura de liquidación (PDF)

La sección **Mis datos desde liquidación** intenta leer el total imponible y
descuentos con un parser determinista sobre el texto del PDF. Si falla, puede
usar la API de xAI (Grok) como respaldo. Ningún monto se aplica al cálculo hasta
que lo confirmes en la interfaz.

### Clave de API de xAI

1. Crea una cuenta en [console.x.ai](https://console.x.ai/) y genera una API key.
2. Define la variable de entorno `XAI_API_KEY`, **o** créala en Streamlit secrets:

```toml
# .streamlit/secrets.toml  (no lo subas al repositorio)
XAI_API_KEY = "tu-clave-aqui"
```

`.streamlit/secrets.toml` ya está en `.gitignore`. Nunca hardcodees la clave en
el código.

Si no hay clave, o la llamada falla, la app sigue funcionando: solo verás los
avisos de “no encontré…” y podrás completar los montos a mano.

### Privacidad

Cuando el lector automático no encuentra un campo, el **texto extraído del PDF**
(no el archivo binario) se envía a la API de xAI para intentar completar esos
montos. No uses esta opción con liquidaciones que no quieras compartir con un
proveedor externo; en ese caso deja la clave sin configurar y escribe los
valores manualmente.
