# Vibetake

🇬🇧 [Read in English](README.md)

Edición por vibe con criterio editorial de verdad: un plugin de Claude Code que lleva
el material crudo a un video terminado — ingesta, corte, mezcla, subtítulos,
gráficos, miniaturas y entrega.

## Instalación

```text
/plugin marketplace add Mattufia/vibetake
/plugin install vibetake@vibetake
```

**Mantenlo actualizado automáticamente**: `/plugin` → **Marketplaces** → `vibetake` →
**Enable auto-update** (viene apagado para marketplaces de terceros). Las versiones
nuevas llegan en los primeros minutos de una sesión y se aplican en la siguiente, o
en el momento con `/reload-plugins`. Para actualizar a mano: `/plugin` →
**Marketplaces** → `vibetake` → **Update marketplace**.

## Preparar una máquina

Ejecuta `/vibetake:setup` una vez por máquina: revisa herramientas, GPU y modelos,
instala lo que falta (winget / Homebrew / apt, después de tu OK) y descarga los modelos
pesados de entrada, con una prueba real de transcripción en la GPU. El perfil de la
máquina va a `~/.vibetake/machine.json`, nunca adentro del plugin.

## Uso: editar un video, paso a paso

### 1. Pon tus archivos en una carpeta — tal como están

Una carpeta por video, con todo suelto adentro: los clips, el audio aparte (un
micrófono de solapa, una grabadora), referencias que te gustan (portadas, otros videos,
capturas) y, si los tienes, el logo, las tipografías o un LUT. No hace falta ordenar
nada: lo primero que hace Vibetake es ordenar la carpeta solo.

```text
mi-video/                         mi-video/
  DJI_0001.MP4                      00-raw/     ← clips, audio, carpetas de la tarjeta
  DJI_0002.MP4          →           brand/      ← logo, tipografías, LUTs
                                    references/ ← estilos que te gustan (nunca se muestran)
  mic.wav                           01-ingest/  02-work/  03-delivery/
  logo.png                          guion.docx  ← todo lo demás queda donde estaba
  guion.docx
```

Solo mueve archivos dentro de la misma carpeta (al instante, no copia ni
recodifica nada), nunca sobrescribe nada y te dice qué movió. Para dejar todo como estaba:
pide "deshaz el orden". Varios clips no son problema: se ordenan por hora de
grabación y se tratan como una sola secuencia; el audio aparte se sincroniza solo.
Si después agregas más archivos, solo se mueven los nuevos. Una foto que no sabe
ubicar (¿una toma para el video o una referencia?) queda donde está, y te pregunta.

### 2. Abre Claude Code en esa carpeta y pide

```text
cd mi-video
claude
```

Pídelo con tus palabras. Cuanto más sabe, menos pregunta:

```text
Edita este video para Instagram Reels y TikTok.
```
```text
Edita este video para YouTube. Está en español; la marca es Acme, el logo está en la carpeta.
Nombres que hay que escribir bien: Vibetake, DaVinci, Ana Pérez.
```
```text
Haz un anuncio de 30 segundos de esto, vertical, con un gancho fuerte y un llamado a la acción.
```

Lo que ayuda: **adónde va** (TikTok, Reels, Shorts, YouTube…), **el idioma**, los
nombres y marcas que aparecen, y los archivos de marca si hay. Lo que no hace falta
decir: subtítulos, ritmo, márgenes, música — lo decide según la plataforma y el tipo
de video, y te cuenta qué eligió.

### 3. Qué pasa

1. **Mira el material** antes de tocar nada: lo transcribe y detecta la cámara, si
   es HDR o log, las caras, las tomas repetidas y las muletillas.
2. **Pregunta solo lo que no puede deducir**, todo junto — normalmente adónde va el
   video y cuánta corrección de color (con vistas antes/después para comparar).
3. **Edita**: corta sobre la transcripción (fuera tomas repetidas y muletillas,
   pausas naturales), reencuadra a vertical si hace falta, corrige el color, limpia y
   mezcla el audio, y agrega subtítulos, gráficos y efectos de sonido según el tipo de
   video.
4. **Revisa el resultado** antes de entregarlo: formato, volumen, sincronía,
   subtítulos dentro de la zona segura — y vuelve a transcribir el archivo final para
   encontrar un corte en medio de una palabra o una frase perdida.

### 4. Qué recibes, en `03-delivery/`

| Archivo | Qué es |
|---|---|
| `mi-video_v1.mp4` | el video (cada ronda de cambios es una versión nueva: `_v2`, `_v3`…) |
| `mi-video.srt` | los subtítulos como archivo, para la accesibilidad y la búsqueda de las plataformas |
| `cover_<plataforma>_<n>.jpg` | tres ideas de portada por plataforma (miniatura de YouTube, portada de Reels/TikTok) |
| `copy.txt` | el texto para publicar, por plataforma: descripción, hashtags, título, capítulos |
| `qa/qa.md` | la revisión final, más recordatorios para subirlo (p. ej. "alta calidad" en Instagram) |

`project.md` guarda qué se decidió y por qué, así el trabajo se puede retomar
después sin empezar de cero.

### 5. Pide cambios

Solo dilo; la edición se guarda como una lista de cortes, así que los cambios
no rehacen todo:

```text
Quita la parte donde hablo del precio.
Empieza con la frase del plazo.
Subtítulos más grandes y sin mayúsculas.
Menos música. Color más cálido. Sin zoom en los cortes.
Otra portada, con el cuadro donde estoy señalando.
```

Cada ronda genera una versión nueva del video; las portadas, los subtítulos y el
texto se actualizan en el lugar.

### Otros comandos

- `/vibetake:setup` — revisar o preparar la máquina (una vez por máquina, o después
  de actualizar un driver o ffmpeg).
- `/vibetake:setup check` — solo ver qué está instalado y listo.

### Si algo se ve mal

Di qué y en qué segundo ("en 0:42 se corta una palabra", "los subtítulos me tapan
la cara en 1:10"). `03-delivery/qa/qa.md` y `project.md` muestran qué se revisó y qué
se decidió.

## Requisitos

- [uv](https://docs.astral.sh/uv/) (corre los scripts e instala sus dependencias de Python la primera vez)
- ffmpeg / ffprobe (una versión completa: libplacebo o zscale para HDR; NVENC/VideoToolbox/QSV/AMF opcionales)
- exiftool (opcional, mejor detección de la cámara)
- Node.js 18+ para la capa de gráficos animados, hecha con [Remotion](https://www.remotion.dev/)
  en cada proyecto. **Licencia de Remotion**: gratis para personas y empresas de hasta
  3 personas; las empresas más grandes necesitan una [licencia de empresa de Remotion](https://www.remotion.dev/license).
- Las tipografías (Google Fonts, licencias abiertas) y los íconos (Simple Icons CC0,
  Lucide ISC, Phosphor MIT) se descargan por proyecto, mientras se edita — nada
  preinstalado
- Opcional, se instala la primera vez que se usa o a mano: rembg (`birefnet-portrait`,
  recorta personas para las portadas, modelo de ~1 GB), realesrgan-ncnn-vulkan
  (agranda con IA los recortes estirados; en el PATH o en `~/.vibetake/tools/realesrgan/`)

## Tests

```text
uv run tests/test_ingest.py        # trampas de ingesta, clips sintéticos
uv run tests/test_transcribe.py    # tomas repetidas, muletillas, tiempos de palabras (necesita el modelo Whisper)
uv run tests/test_grade.py         # color: balance de blancos del ambiente, camino log, niveles, el LUT del usuario
uv run tests/test_match.py         # igualar tomas: grupos, referencias, oscuras conservadas, omitir, ajustes, reglas
uv run tests/test_edit.py          # cortes y render: sincronía A/V, sin clics, fuera tomas repetidas, cortes en silencio
uv run tests/test_joins.py         # cada unión de la vista previa: hojas, un reel corto, saltos chicos/de luz/de nivel, destellos
uv run tests/test_take_quality.py  # dos tomas de una frase comparadas: más blanda, más movida, quemada; un look compartido no dice nada
uv run tests/test_reframe.py       # encuadre: completo/fijo/operador por segmento; suavizados, cortes; acercamientos, zoom de impacto
uv run tests/test_speaker.py       # varias personas: quién dice cada frase (micrófonos, bocas), el encuadre las sigue
uv run tests/test_broll.py         # b-roll anclado a palabras, el sonido debajo, sigue los re-cortes
uv run tests/test_audio.py         # limpieza sobre el crudo (ruido, nivel, sin retraso), volumen por destino, ducking, musicgen
uv run tests/test_audio_inputs.py  # el origen del sonido: canales, mejor pista, grabaciones aparte, ambiente
uv run tests/test_subs.py          # reglas de .srt, grupos de palabras, esquivar caras, subtítulos quemados
uv run tests/test_qa.py            # QA de entrega: un archivo bueno pasa, las fallas plantadas se detectan
uv run tests/test_graphics.py      # hoja de cues, SFX sintetizados sobre su palabra, capa de gráficos, subtítulos apagados
uv run tests/test_delivery.py      # versiones, chequeo del copy, bordes de corte en el audio, chequeo de escucha
uv run tests/test_setup.py         # perfil de la máquina, plan de instalación, herramientas fuera del PATH
uv run tests/test_organize.py      # archivos sueltos → 00-raw/ y brand/, nada sobrescrito, deshacer
uv run tests/test_refs.py          # referencias hechas visibles, ritmo medido de un video de referencia
uv run tests/test_cut_rules.py     # reacciones vs muletillas, tramos en silencio, ritmo natural, rangos protegidos
uv run tests/test_design.py        # tipografías e íconos del proyecto, paleta, capa Remotion con diseño + chequeo de zona segura (red, Node)
uv run tests/test_stage.py         # composición: la edición puesta en un escenario, sincronizada al cuadro, componentes de página y marco (Node)
uv run tests/test_timing.py        # velocidad, rampas, congelados, reversa, boomerang, cortes J/L, transiciones, fundidos: cuadro a cuadro
uv run tests/test_effects_edges.py # transiciones junto a cortes secos, L+J en un segmento, congelado en 0, suave, J/L como se reproduce
uv run tests/test_montage.py       # montaje con música: momentos, cortes en el beat, planos más cortos cuando sube, bloques alrededor del habla
uv run tests/test_shorts.py        # shorts de un video largo: rangos sin tomas repetidas, gancho, corte/subtítulos/render propios, QA por short
uv run tests/test_translate.py     # .srt traducido: mismos tiempos, cues muy rápidos o muy largos marcados, CJK sin espacios
uv run tests/test_screen.py        # zooms de pantalla: el menú y lo que se escribe, nunca el cursor ni un cambio de página; suavizados, deslizamientos, vertical
uv run tests/test_ingest_edges.py  # material raro: nombres repetidos, sin sonido/sin audio, archivos rotos, proxies, sin duración, bloqueo, falsas tomas repetidas
uv run tests/test_cut_edges.py     # orden de corte: ganchos en su lugar, protecciones por variante, silencios donde se listan, ataduras
uv run tests/test_render_edges.py  # el upscale conserva desenfoque y efectos, desenfoque fijo sobre los bordes, 100+ tramos, desfase de audio, apóstrofos
uv run tests/test_look.py          # encajar sobre una copia desenfocada, espejar/rotar/inclinar, chroma key, cajas de desenfoque con seguimiento, estabilización, look
uv run tests/test_audio_fx.py      # palabras censuradas (pitido/silencio, subtítulos con asteriscos), detección de beat, efectos de voz, capítulos en el MP4, GIF
uv run tests/test_compose.py       # transiciones whip/push, imagen en imagen, pantalla dividida, gráficos detrás de la persona
uv run tests/test_upscale.py       # agrandado con IA de segmentos estirados (se omite sin Real-ESRGAN)
uv run tests/test_fixes.py         # regresiones: el re-corte conserva ubicaciones, capas viejas, HDR sin corrección, log mezclado
uv run tests/test_readme.py        # README.md y README.es.md tienen la misma estructura
```

Cada test imprime PASS/FAIL por chequeo. La mayoría arma sus propios clips
sintéticos; algunos necesitan el modelo Whisper, Node o la red, como se indica.

## Cómo contribuir

Ideas, bugs y pull requests son bienvenidos.

- **Un bug o una idea**: abre un issue. Si un video salió mal, di qué y en qué
  segundo, y adjunta `03-delivery/qa/qa.md` si puedes.
- **Una cámara que no se detecta**: ejecuta `uv run tools/camera_report.py <clip>` sobre
  un archivo original de la cámara y pega la salida en un issue.
- **Un pull request**: que se enfoque en una sola cosa, agrega o actualiza un test en
  `tests/` que falle sin tu cambio, y ejecuta los tests de arriba antes de enviarlo.
- **Documentación**: este README es la copia en español de [`README.md`](README.md).
  Un cambio en uno va también en el otro, en el mismo pull request (`uv run tests/test_readme.py`
  revisa que los dos tengan la misma estructura).

## Licencia

[MIT](LICENSE). Remotion, que se usa para la capa de gráficos, tiene su propia
licencia (ver Requisitos).
