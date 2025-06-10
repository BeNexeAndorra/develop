import os
import sys
from pydub import AudioSegment
import tempfile
import time
import logging
import numpy as np
import librosa
import soundfile as sf # Para leer/escribir archivos de audio con librosa
import random # Para decisiones "creativas" en las transiciones
import math # Para funciones de fundido
from scipy.signal import butter, lfilter # Para filtros básicos (simulación de EQ)

# --- INICIO DE LA MODIFICACIÓN (Añadir estas líneas) ---
# Establecer la ruta a los ejecutables de FFmpeg para pydub
# Esto es específico para macOS y Homebrew. Ajusta si tu FFmpeg está en otro lugar.
AudioSegment.converter = "/usr/local/bin/ffmpeg"
AudioSegment.ffmpeg = "/usr/local/bin/ffmpeg"
AudioSegment.ffprobe = "/usr/local/bin/ffprobe"
# --- FIN DE LA MODIFICACIÓN ---

# Configuración del logger para mixing_engine
mixing_logger = logging.getLogger('mixing_engine_module')
mixing_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
mixing_logger.addHandler(handler)

# Constantes para la mezcla
SHORT_TRANSITION_MS = 4 * 1000  # 4 segundos para cortes o fades rápidos
MEDIUM_TRANSITION_MS = 8 * 1000  # 8 segundos para crossfades estándar
LONG_TRANSITION_MS = 16 * 1000  # 16 segundos para mezclas más largas
OVERLAP_TRANSITION_MS = 32 * 1000 # 32 segundos para superposición y EQ

# Mínimo de duración de pista para considerar para una transición (ej. no mezclar los primeros segundos)
MIN_TRACK_MIX_START_MS = 15 * 1000  # No empezar a mezclar en los primeros 15 segundos del track entrante
MIN_TRACK_MIX_END_MS = 15 * 1000   # No mezclar los últimos 15 segundos del track saliente (a menos que sea necesario)


# Directorio base donde se asume que están los archivos de audio subidos
# Esta variable ya no se usará directamente para encontrar archivos,
# sino que las pistas de la playlist deben venir con 'filepath' ya resuelto.
# Se mantiene como referencia si se desea un comportamiento de fallback para MP3s subidos.
UPLOAD_FOLDER = os.path.join(tempfile.gettempdir(), 'dj_uploads')


def adjust_tempo_librosa(audio_segment: AudioSegment, target_bpm: float, current_bpm: float) -> AudioSegment:
    """
    Ajusta el tempo de una pista usando librosa.effects.time_stretch para preservar el tono.
    Convierte el segmento de pydub a numpy array, aplica time-stretching con librosa,
    y convierte el resultado de vuelta a un AudioSegment de pydub.
    """
    if current_bpm is None or target_bpm is None or current_bpm == 0:
        mixing_logger.warning("BPM actual o objetivo no válido para ajuste de tempo. Retornando segmento original.")
        return audio_segment

    ratio = target_bpm / current_bpm
    if ratio == 1:
        return audio_segment # No es necesario ajustar si los BPM son los mismos

    mixing_logger.debug(f"Ajustando tempo de {current_bpm:.2f} BPM a {target_bpm:.2f} BPM (ratio: {ratio:.2f}).")

    # Convertir AudioSegment a numpy array
    # pydub.AudioSegment.get_array_of_samples() devuelve un array de enteros
    y = np.array(audio_segment.get_array_of_samples()).astype(np.float32)
    sr = audio_segment.frame_rate

    # Si el audio es estéreo, librosa espera un array de una dimensión para el análisis
    # y el time-stretching. Promediamos los canales si es estéreo para el análisis
    # y luego lo aplicamos al AudioSegment completo.
    if audio_segment.channels == 2:
        # Pydub devuelve los canales entrelazados (LRLR...), los separamos
        y_left = y[0::2]
        y_right = y[1::2]
        y_mono = (y_left + y_right) / 2.0 # Convertir a mono para librosa

        # Aplicar time-stretch al audio mono
        y_stretched_mono = librosa.effects.time_stretch(y_mono, rate=ratio)

        # Para reconstruir un audio estéreo, podemos duplicar el canal mono estirado
        # o aplicar el estiramiento a cada canal por separado y luego entrelazar.
        # Duplicar el canal mono estirado es más sencillo y suele ser suficiente.
        y_stretched = np.empty((y_stretched_mono.size * 2,), dtype=y_stretched_mono.dtype)
        y_stretched[0::2] = y_stretched_mono
        y_stretched[1::2] = y_stretched_mono
        output_channels = 2
    else:
        # Si es mono, aplicar directamente
        y_stretched = librosa.effects.time_stretch(y, rate=ratio)
        output_channels = 1
    
    # Convertir el array de numpy a un AudioSegment
    # Asegúrate de que el tipo de datos sea compatible con pydub (int16 para AudioSegment por defecto)
    # y escalar si es necesario.
    # librosa devuelve floats, pydub espera ints. Multiplicar por 2^15 para int16.
    max_val = np.iinfo(np.int16).max
    y_stretched = (y_stretched * max_val).astype(np.int16)

    stretched_audio_segment = AudioSegment(
        y_stretched.tobytes(), 
        frame_rate=sr,
        sample_width=audio_segment.sample_width,
        channels=output_channels
    )
    return stretched_audio_segment

def get_bpm_adjusted_transition_duration(bpm: float) -> int:
    """
    Calcula una duración de transición en ms basada en el BPM para que sea a tempo.
    Aproximadamente 8, 16 o 32 beats para transiciones comunes.
    """
    if bpm is None or bpm <= 0:
        return MEDIUM_TRANSITION_MS # Fallback

    # Duración de un beat en milisegundos
    beat_duration_ms = 60000 / bpm

    # Transiciones basadas en un número de beats (ej. 8, 16, 32 beats)
    # Se elige un número de beats que resulte en una transición de duración razonable.
    # Por ejemplo, 16 beats.
    transition_beats = 16 
    calculated_duration_ms = int(beat_duration_ms * transition_beats)

    # Asegurarse de que la duración esté dentro de límites razonables
    return max(SHORT_TRANSITION_MS, min(calculated_duration_ms, LONG_TRANSITION_MS * 2)) # Un poco más flexible

def apply_eq(audio_segment: AudioSegment, eq_type: str, freq: float, q: float = 1.0, gain_db: float = 0.0) -> AudioSegment:
    """
    Aplica una simulación básica de ecualización (filtro Butterworth) a un AudioSegment.
    eq_type: 'lowpass', 'highpass', 'bandpass', 'lowshelf', 'highshelf'.
    freq: Frecuencia de corte para lowpass/highpass, o frecuencia central para bandpass/shelf.
    q: Factor de calidad para bandpass/shelf.
    gain_db: Ganancia en dB para shelf.
    """
    # Convertir AudioSegment a numpy array
    y = np.array(audio_segment.get_array_of_samples()).astype(np.float32)
    sr = audio_segment.frame_rate
    channels = audio_segment.channels

    if channels == 2:
        # Si es estéreo, se procesa cada canal por separado o se convierte a mono, procesa y duplica.
        # Para simplicidad y rendimiento, promediamos a mono, aplicamos EQ y luego duplicamos.
        y_mono = (y[0::2] + y[1::2]) / 2.0
    else:
        y_mono = y

    nyquist = 0.5 * sr
    normalized_freq = freq / nyquist

    if eq_type == 'lowpass':
        b, a = butter(4, normalized_freq, btype='low', analog=False)
    elif eq_type == 'highpass':
        b, a = butter(4, normalized_freq, btype='high', analog=False)
    # Otros tipos de EQ (shelf, bandpass) requieren implementaciones más complejas o librerías especializadas
    # como `scipy.signal.iirfilter` con tipos 'lowshelf'/'highshelf' que son más complejos de usar
    # directamente sin un conocimiento profundo de diseño de filtros.
    # Por ahora, nos quedamos con lowpass y highpass, que son los más comunes para crossfades.
    else:
        mixing_logger.warning(f"Tipo de EQ '{eq_type}' no soportado para simulación básica. Saltando EQ.")
        return audio_segment # Retorna el audio original si el tipo no es soportado

    # Aplicar el filtro
    y_filtered_mono = lfilter(b, a, y_mono)

    # Reconvertir a estéreo si era originalmente estéreo
    if channels == 2:
        y_filtered = np.empty((y_filtered_mono.size * 2,), dtype=y_filtered_mono.dtype)
        y_filtered[0::2] = y_filtered_mono
        y_filtered[1::2] = y_filtered_mono
    else:
        y_filtered = y_filtered_mono

    # Escalar y convertir a int16 para pydub
    max_val = np.iinfo(np.int16).max
    y_filtered = (y_filtered / np.max(np.abs(y_filtered)) * max_val).astype(np.int16) # Normalizar antes de convertir

    return AudioSegment(
        y_filtered.tobytes(),
        frame_rate=sr,
        sample_width=audio_segment.sample_width,
        channels=channels
    )


def create_mix(playlist: list, output_folder: str, mix_duration_minutes: int, progress_callback=None) -> tuple[AudioSegment, str]:
    """
    Crea una mezcla de audio a partir de una lista de pistas de análisis.
    playlist: Lista de diccionarios de resultados de análisis de audio.
    output_folder: Carpeta donde se guardará el archivo de mezcla final.
    mix_duration_minutes: Duración deseada de la mezcla final en minutos.
    progress_callback: Función para reportar el progreso (progreso_%, mensaje).
    Retorna el AudioSegment de la mezcla final y el nombre del archivo.
    """
    if not playlist:
        mixing_logger.error("La playlist está vacía. No se puede crear la mezcla.")
        if progress_callback:
            progress_callback(100, "Error: Playlist vacía.", True)
        return None, None

    mixing_logger.info(f"Creando mezcla para {len(playlist)} pistas. Duración objetivo: {mix_duration_minutes} minutos.")
    
    target_mix_duration_ms = mix_duration_minutes * 60 * 1000
    final_mix = AudioSegment.empty()
    
    # Cargar la primera pista
    current_track_info = playlist[0]
    try:
        current_track_path = current_track_info['filepath']
        current_audio = AudioSegment.from_file(current_track_path)
        
        # Ajustar el tempo de la primera pista si es necesario (ej. a un BPM inicial deseado, o simplemente al propio BPM)
        # Para la primera pista, la mantenemos como está o la ajustamos a su propio BPM detectado si es un objetivo.
        # Aquí la mantendremos tal cual y ajustaremos las siguientes.
        
        final_mix += current_audio[:min(current_audio.duration_recognizable, current_audio.duration_seconds * 1000)] # Añadir los primeros segundos o duración completa si es corta
        
        mixing_logger.info(f"Añadida la primera pista: {current_track_info['filename']}")

    except Exception as e:
        error_msg = f"Error al cargar la primera pista '{current_track_info.get('filename', 'N/A')}': {e}"
        mixing_logger.error(error_msg, exc_info=True)
        if progress_callback:
            progress_callback(100, f"Error al cargar la primera pista: {e}", True)
        return None, None

    processed_tracks_count = 1
    total_tracks = len(playlist)

    for i in range(total_tracks - 1):
        if final_mix.duration_seconds * 1000 >= target_mix_duration_ms:
            mixing_logger.info(f"Duración de mezcla objetivo ({mix_duration_minutes} min) alcanzada. Terminando mezcla.")
            break
        
        current_track_info = playlist[i]
        next_track_info = playlist[i+1]
        
        progress_percentage = int((processed_tracks_count / total_tracks) * 100)
        if progress_callback:
            progress_callback(max(5, min(95, progress_percentage)), f"Mezclando pista {processed_tracks_count+1} de {total_tracks}...")

        mixing_logger.info(f"Preparando transición de '{current_track_info['filename']}' a '{next_track_info['filename']}'")

        try:
            next_audio_original = AudioSegment.from_file(next_track_info['filepath'])

            # 1. Ajuste de Tempo para la pista entrante
            next_audio_adjusted = adjust_tempo_librosa(
                next_audio_original,
                target_bpm=current_track_info['bpm'], # Intentar que la siguiente pista se adapte al BPM de la actual
                current_bpm=next_track_info['bpm']
            )
            mixing_logger.debug(f"Tempo de '{next_track_info['filename']}' ajustado a {current_track_info['bpm']:.2f} BPM.")

            # 2. Determinar la duración de la transición
            transition_duration_ms = get_bpm_adjusted_transition_duration(current_track_info['bpm'])
            mixing_logger.debug(f"Duración de transición calculada: {transition_duration_ms / 1000:.2f}s")
            
            # Asegurarse de que las pistas son lo suficientemente largas para la transición
            # y que la duración de la transición no excede la duración de la pista entrante o saliente
            # (evitando errores si las pistas son muy cortas)
            actual_transition_duration = min(transition_duration_ms, 
                                            current_audio.duration_seconds * 1000 - MIN_TRACK_MIX_END_MS,
                                            next_audio_adjusted.duration_seconds * 1000 - MIN_TRACK_MIX_START_MS)
            
            # La duración mínima de una transición si las pistas son muy cortas, para evitar errores
            if actual_transition_duration < SHORT_TRANSITION_MS:
                actual_transition_duration = SHORT_TRANSITION_MS
            
            mixing_logger.debug(f"Duración de transición efectiva: {actual_transition_duration / 1000:.2f}s")


            # 3. Preparar segmentos para la mezcla
            # Parte final de la pista actual que se mezclará
            # Asegurarse de no ir a un índice negativo
            crossfade_out_start = max(0, len(current_audio) - actual_transition_duration)
            segment_out = current_audio[crossfade_out_start:]

            # Parte inicial de la siguiente pista que se mezclará
            # Asegurarse de no ir a un índice más allá de la duración de la pista
            crossfade_in_end = min(len(next_audio_adjusted), actual_transition_duration)
            segment_in = next_audio_adjusted[:crossfade_in_end]

            # Si la pista entrante no tiene suficiente duración para la transición,
            # tomar lo que haya disponible.
            if len(next_audio_adjusted) < MIN_TRACK_MIX_START_MS + actual_transition_duration:
                segment_in = next_audio_adjusted[MIN_TRACK_MIX_START_MS:]
            else:
                segment_in = next_audio_adjusted[MIN_TRACK_MIX_START_MS : MIN_TRACK_MIX_START_MS + actual_transition_duration]

            # 4. Aplicar fundidos y EQ (simulación)
            # Fundido de salida para la pista actual
            segment_out = segment_out.fade_out(actual_transition_duration)
            # Opcional: EQ para el track saliente (ej. lowpass para cortar graves)
            # segment_out = apply_eq(segment_out, 'lowpass', 150) # Corta graves

            # Fundido de entrada para la siguiente pista
            segment_in = segment_in.fade_in(actual_transition_duration)
            # Opcional: EQ para el track entrante (ej. highpass para cortar agudos temporalmente)
            # segment_in = apply_eq(segment_in, 'highpass', 5000) # Corta agudos

            # 5. Realizar la mezcla (crossfade)
            # Se usa el crossfade de pydub. Si los segmentos tienen diferentes duraciones
            # pydub lo maneja, pero es mejor que sean del mismo tamaño para el crossfade.
            # Nos aseguramos de que el crossfade ocurre sobre la duración calculada.
            
            # La parte final del 'final_mix' existente es el `current_audio` que debe salir.
            # Removemos la duración de la transición del final_mix para superponer.
            final_mix = final_mix[:-actual_transition_duration]
            
            # Superponer el segmento saliente y el entrante
            # crossfade(self, other, duration, fade_by_proportion)
            # Si se usa crossfade, pydub lo maneja.
            # Aquí, lo que hacemos es adjuntar el resto del track actual sin la parte de crossfade_out.
            # Y luego le añadimos el track entrante con un crossfade.
            
            # Opción 1: Superponer directamente con crossfade
            mixed_segment = segment_out.overlay(segment_in)
            final_mix += mixed_segment

            # Opción 2 (más simple si el crossfade de pydub no funcionara bien):
            # final_mix += segment_out.append(segment_in, crossfade=actual_transition_duration)
            # Esta es la forma más directa de pydub de hacer crossfade.

            # Añadir el resto de la pista entrante
            # Empezar desde donde termina el segmento de entrada ya mezclado
            remaining_next_audio = next_audio_adjusted[MIN_TRACK_MIX_START_MS + actual_transition_duration:]
            final_mix += remaining_next_audio
            
            mixing_logger.info(f"Transición completada de '{current_track_info['filename']}' a '{next_track_info['filename']}'.")

            current_audio = next_audio_adjusted # La pista ajustada se convierte en la "actual" para la siguiente iteración
            processed_tracks_count += 1

        except Exception as e:
            error_msg = f"Error durante la transición de '{current_track_info.get('filename', 'N/A')}' a '{next_track_info.get('filename', 'N/A')}': {e}"
            mixing_logger.error(error_msg, exc_info=True)
            # Intentar continuar con la siguiente pista si es posible, o finalizar la mezcla
            if progress_callback:
                progress_callback(90, f"Error en la transición. Intentando continuar. Error: {e}", True)
            # Si falla una transición, simplemente añadimos el resto de la pista actual y la siguiente completa
            # para no romper el bucle por completo.
            final_mix += current_audio[len(current_audio) - 5000:] # Añadir últimos 5 segundos del actual
            final_mix += next_audio_original # Añadir la siguiente pista sin ajustar
            current_audio = next_audio_original # La original se convierte en la actual

    # Asegurarse de que la mezcla final no exceda la duración deseada.
    if final_mix.duration_seconds * 1000 > target_mix_duration_ms:
        final_mix = final_mix[:target_mix_duration_ms]
        mixing_logger.info(f"Recortada la mezcla final a la duración objetivo: {mix_duration_minutes} minutos.")

    # Exportar la mezcla final
    output_filename = f"mixed_playlist_{int(time.time())}.mp3"
    output_filepath = os.path.join(output_folder, output_filename)

    try:
        mixing_logger.info(f"Exportando mezcla final a: {output_filepath}")
        final_mix.export(output_filepath, format="mp3", bitrate="192k") # Calidad razonable
        mixing_logger.info("Mezcla final exportada con éxito.")
        if progress_callback:
            progress_callback(100, "Mezcla completada.", False)
        return final_mix, output_filename
    except Exception as e:
        error_msg = f"Error al exportar la mezcla final: {e}"
        mixing_logger.error(error_msg, exc_info=True)
        if progress_callback:
            progress_callback(100, f"Error al exportar la mezcla: {e}", True)
        return None, None

if __name__ == '__main__':
    # --- PRUEBA DE FUNCIONALIDAD DEL MIXING ENGINE ---
    mixing_logger.info("Iniciando prueba del mixing_engine.py")

    # Crear directorios temporales para la prueba
    test_upload_dir = os.path.join(tempfile.gettempdir(), 'dj_uploads_test')
    test_output_dir = os.path.join(tempfile.gettempdir(), 'dj_mixes_test')
    os.makedirs(test_upload_dir, exist_ok=True)
    os.makedirs(test_output_dir, exist_ok=True)

    # Crear archivos de audio dummy para la prueba (silencios con diferentes BPMs simulados)
    mixing_logger.info("Generando archivos MP3 dummy para la prueba...")
    test_track1_path = os.path.join(test_upload_dir, 'test_track1.mp3')
    test_track2_path = os.path.join(test_upload_dir, 'test_track2.mp3')
    test_track3_path = os.path.join(test_upload_dir, 'test_track3.mp3')
    test_track4_path = os.path.join(test_upload_dir, 'test_track4.mp3')

    # Generar segmentos de audio dummy con diferentes duraciones y 'BPM' simulados
    # AudioSegment.silent(duration_ms, frame_rate=44100)
    # Necesitamos asignarles un 'bpm' para que adjust_tempo_librosa no falle.
    dummy_audio1 = AudioSegment.silent(duration=30 * 1000, frame_rate=44100).set_channels(2)
    dummy_audio2 = AudioSegment.silent(duration=30 * 1000, frame_rate=44100).set_channels(2)
    dummy_audio3 = AudioSegment.silent(duration=30 * 1000, frame_rate=44100).set_channels(2)
    dummy_audio4 = AudioSegment.silent(duration=30 * 1000, frame_rate=44100).set_channels(2)

    # Exportar a MP3. FFmpeg es necesario aquí.
    try:
        dummy_audio1.export(test_track1_path, format="mp3")
        dummy_audio2.export(test_track2_path, format="mp3")
        dummy_audio3.export(test_track3_path, format="mp3")
        dummy_audio4.export(test_track4_path, format="mp3")
        mixing_logger.info("Archivos MP3 dummy generados.")
    except Exception as e:
        mixing_logger.error(f"Error al exportar archivos MP3 dummy. Asegúrate de que FFmpeg está instalado y en tu PATH. Error: {e}")
        sys.exit(1) # Salir si no podemos generar los archivos de prueba

    # Simular resultados de análisis (filepath es crucial)
    dummy_playlist = [
        {'filename': 'test_track1.mp3', 'filepath': test_track1_path, 'bpm': 120.0, 'key': 'Cmaj', 'camelot_key': '8B', 'energy': 0.7, 'duration': 30.0},
        {'filename': 'test_track2.mp3', 'filepath': test_track2_path, 'bpm': 125.0, 'key': 'Gmaj', 'camelot_key': '9B', 'energy': 0.8, 'duration': 30.0},
        {'filename': 'test_track3.mp3', 'filepath': test_track3_path, 'bpm': 122.0, 'key': 'Dmaj', 'camelot_key': '10B', 'energy': 0.75, 'duration': 30.0},
        {'filename': 'test_track4.mp3', 'filepath': test_track4_path, 'bpm': 128.0, 'key': 'Amin', 'camelot_key': '8A', 'energy': 0.85, 'duration': 30.0},
    ]

    # Callback de progreso para la prueba
    def test_progress_callback(progress, message, is_error=False):
        mixing_logger.info(f"Progreso de mezcla: {progress}% - {message} {'(ERROR)' if is_error else ''}")

    try:
        # Generar la mezcla
        mixing_logger.info("Llamando a create_mix con la playlist dummy.")
        mix_segment, mix_filename = create_mix(
            playlist=dummy_playlist, 
            output_folder=test_output_dir, 
            mix_duration_minutes=2, # Mezcla de 2 minutos para la prueba
            progress_callback=test_progress_callback
        )

        if mix_segment and mix_filename:
            mixing_logger.info(f"Mezcla de prueba creada con éxito: {mix_filename}")
            mixing_logger.info(f"Duración de la mezcla de prueba: {mix_segment.duration_seconds:.2f} segundos.")
        else:
            mixing_logger.error("Fallo al crear la mezcla de prueba.")

    except Exception as e:
        mixing_logger.critical(f"Error crítico durante la prueba de mixing_engine.py: {e}", exc_info=True)
        mixing_logger.critical("Asegúrate de que FFmpeg está instalado, accesible y que los archivos de prueba son válidos.")

    finally:
        # Limpiar archivos dummy y directorios temporales
        mixing_logger.info("Limpiando archivos temporales de prueba...")
        for f_name in ["test_track1.mp3", "test_track2.mp3", "test_track3.mp3", "test_track4.mp3"]:
            f_path = os.path.join(test_upload_dir, f_name)
            if os.path.exists(f_path):
                try:
                    os.remove(f_path)
                    mixing_logger.info(f"Archivo dummy limpiado: {f_path}")
                except Exception as e:
                    mixing_logger.warning(f"No se pudo eliminar el archivo dummy {f_path}: {e}")
        
        if 'mix_path' in locals() and os.path.exists(mix_path): # Asegúrate de que mix_path se haya definido
            try:
                os.remove(mix_path)
                mixing_logger.info(f"Mezcla de prueba limpiada: {mix_path}")
            except Exception as e:
                mixing_logger.warning(f"No se pudo eliminar la mezcla de prueba {mix_path}: {e}")
        
        # Solo borrar los directorios si están vacíos
        if os.path.exists(test_output_dir):
            try:
                os.rmdir(test_output_dir) # Solo borrar si está vacío
                mixing_logger.info(f"Directorio de prueba de mezcla limpiado: {test_output_dir}")
            except OSError:
                mixing_logger.warning(f"El directorio de prueba de mezcla {test_output_dir} no está vacío. No se pudo borrar.")
        
        if os.path.exists(test_upload_dir):
            try:
                os.rmdir(test_upload_dir)
                mixing_logger.info(f"Directorio de subida de prueba limpiado: {test_upload_dir}")
            except OSError:
                mixing_logger.warning(f"El directorio de subida de prueba {test_upload_dir} no está vacío. No se pudo borrar.")

    mixing_logger.info("Prueba del mixing_engine.py finalizada.")