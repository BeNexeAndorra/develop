import json
import logging
import sys
import math
import random # Para la selección aleatoria de la pista inicial

# Configuración del logger para playlist_generation
playlist_logger = logging.getLogger('playlist_generation_module')
playlist_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
playlist_logger.addHandler(handler)

# Mapeo de claves Camelot (valores A son menores, B son mayores)
# Los círculos concéntricos de la rueda de Camelot
CAMELOT_CIRCLE_OF_FIFTHS = {
    # Menores (A)
    1: 'Abm', 2: 'Ebm', 3: 'Bbm', 4: 'Fm', 5: 'Cm', 6: 'Gm', 7: 'Dm', 8: 'Am', 9: 'Em', 10: 'Bm', 11: 'F#m', 12: 'Dbm',
    # Mayores (B)
    1: 'B', 2: 'F#', 3: 'Db', 4: 'Ab', 5: 'Eb', 6: 'Bb', 7: 'F', 8: 'C', 9: 'G', 10: 'D', 11: 'A', 12: 'E'
}

# Inverso para buscar el número Camelot dado la clave musical (ej: 'Cmaj' -> 8B)
# Adaptado para que sea compatible con los resultados de audio_analysis.py
CAMELOT_KEY_MAP = {
    '8B': ('C', 'major'), '3B': ('C#', 'major'), '10B': ('D', 'major'), '5B': ('D#', 'major'),
    '12B': ('E', 'major'), '7B': ('F', 'major'), '2B': ('F#', 'major'), '9B': ('G', 'major'),
    '4B': ('G#', 'major'), '11B': ('A', 'major'), '6B': ('A#', 'major'), '1B': ('B', 'major'),

    '5A': ('Cm', 'minor'), '12A': ('C#m', 'minor'), '7A': ('Dm', 'minor'), '2A': ('D#m', 'minor'),
    '9A': ('Em', 'minor'), '4A': ('Fm', 'minor'), '11A': ('F#m', 'minor'), '6A': ('Gm', 'minor'),
    '1A': ('G#m', 'minor'), '8A': ('Am', 'minor'), '3A': ('A#m', 'minor'), '10A': ('Bm', 'minor')
}

def get_camelot_number_and_mode(camelot_key: str) -> tuple[int, str]:
    """
    Extrae el número y el modo (A/B) de una clave Camelot.
    Ej: '8B' -> (8, 'B')
    """
    if not camelot_key or len(camelot_key) < 2:
        return None, None
    try:
        number = int(camelot_key[:-1])
        mode = camelot_key[-1].upper()
        return number, mode
    except ValueError:
        return None, None

def are_harmonically_compatible(key1_camelot: str, key2_camelot: str) -> bool:
    """
    Comprueba si dos claves Camelot son armónicamente compatibles.
    Compatibles si:
    1. Son la misma clave (ej: 8B y 8B).
    2. Están adyacentes en el círculo de quintas (ej: 8B y 9B, 8B y 7B).
    3. Son relativas mayores/menores (mismo número, diferente modo, ej: 8A y 8B).
    """
    num1, mode1 = get_camelot_number_and_mode(key1_camelot)
    num2, mode2 = get_camelot_number_and_mode(key2_camelot)

    if None in [num1, mode1, num2, mode2]:
        playlist_logger.debug(f"Claves Camelot inválidas para compatibilidad: {key1_camelot}, {key2_camelot}")
        return False

    # 1. Misma clave
    if num1 == num2 and mode1 == mode2:
        return True

    # 2. Adyacentes en el círculo de quintas (mismo modo, número +/- 1 o 12/1)
    if mode1 == mode2:
        if abs(num1 - num2) == 1 or \
           (num1 == 1 and num2 == 12) or \
           (num1 == 12 and num2 == 1):
            return True

    # 3. Relativas mayores/menores (mismo número, diferente modo)
    if num1 == num2 and mode1 != mode2:
        return True
    
    return False

def calculate_bpm_difference_score(bpm1: float, bpm2: float) -> float:
    """
    Calcula una puntuación basada en la diferencia de BPMs.
    Menor diferencia, mayor puntuación. Penaliza fuertemente grandes diferencias.
    """
    if bpm1 is None or bpm2 is None or bpm1 == 0 or bpm2 == 0:
        return 0.0 # No se puede calcular si falta BPM

    # Permite una pequeña tolerancia de BPM
    if abs(bpm1 - bpm2) <= 2:
        return 1.0 # Muy compatible en BPM

    # Calcula el ratio, siempre >= 1
    ratio = max(bpm1, bpm2) / min(bpm1, bpm2)

    # Una diferencia de 10% (ratio 1.1) ya empieza a ser considerable.
    # Usar una función de decaimiento (ej. exponencial o lineal inverso)
    # Ejemplo: 1 / (1 + (ratio - 1) * factor)
    # Donde 'factor' controla la penalización.
    bpm_score = 1.0 / (1.0 + (ratio - 1) * 10) # 10 es un factor de penalización arbitrario
    return max(0.0, bpm_score) # Asegurar que la puntuación no sea negativa

def calculate_energy_difference_score(energy1: float, energy2: float) -> float:
    """
    Calcula una puntuación basada en la diferencia de energía.
    Pistas con energía similar tienen mayor puntuación.
    """
    if energy1 is None or energy2 is None:
        return 0.0 # No se puede calcular si falta energía

    diff = abs(energy1 - energy2)
    # Normaliza la diferencia a un rango de 0 a 1 (asumiendo energía en [0, 1])
    # energy_score = 1 - diff (si diff max es 1)
    # O usar una función de decaimiento similar a BPM
    energy_score = 1.0 / (1.0 + diff * 5) # 5 es un factor de penalización arbitrario
    return max(0.0, energy_score)

def score_transition(current_track: dict, next_track: dict) -> float:
    """
    Calcula una puntuación de compatibilidad para la transición entre dos pistas.
    Una puntuación más alta indica una mejor transición.
    """
    score = 0.0

    # 1. Compatibilidad armónica (muy importante)
    key1 = current_track.get('camelot_key')
    key2 = next_track.get('camelot_key')

    if key1 and key2 and are_harmonically_compatible(key1, key2):
        score += 5.0 # Puntuación alta para compatibilidad armónica
    else:
        score -= 2.0 # Penalización si no son compatibles

    # 2. Compatibilidad de BPM (importante)
    bpm1 = current_track.get('bpm')
    bpm2 = next_track.get('bpm')
    bpm_score = calculate_bpm_difference_score(bpm1, bpm2)
    score += bpm_score * 3.0 # Peso medio para BPM

    # 3. Compatibilidad de Energía (menos importante, pero ayuda a la fluidez)
    energy1 = current_track.get('energy')
    energy2 = next_track.get('energy')
    energy_score = calculate_energy_difference_score(energy1, energy2)
    score += energy_score * 1.0 # Peso bajo para energía

    # Ajustar puntuación para evitar negativos grandes
    return max(0.0, score)


def find_next_track(current_track: dict, remaining_tracks: list) -> dict | None:
    """
    Encuentra la mejor pista siguiente de las restantes basándose en la puntuación de transición.
    """
    best_next_track = None
    best_score = -float('inf')

    for next_track in remaining_tracks:
        # Asegurarse de que el track tiene los datos mínimos para el análisis
        if not all(k in next_track and next_track[k] is not None for k in ['bpm', 'camelot_key', 'energy', 'filepath']):
            playlist_logger.debug(f"Pista '{next_track.get('filename', 'N/A')}' omitida por datos incompletos.")
            continue
            
        score = score_transition(current_track, next_track)
        
        # Preferir pistas no usadas recientemente si la puntuación es similar
        # (Esto es una heurística simple, puede ser más complejo con historial)
        if score > best_score:
            best_score = score
            best_next_track = next_track
        elif score == best_score and best_next_track:
            # Si hay un empate, elegir una al azar o usar alguna otra heurística
            if random.choice([True, False]): # 50/50
                best_next_track = next_track

    return best_next_track


def generate_playlist(analyzed_tracks: list, mix_duration_minutes: int = 30) -> list:
    """
    Genera una lista de reproducción optimizada para la mezcla.
    analyzed_tracks: Lista de diccionarios con los resultados del análisis de audio.
    mix_duration_minutes: Duración deseada de la mezcla en minutos.
    Retorna una lista de pistas ordenadas para la mezcla.
    """
    playlist_logger.info(f"Iniciando generación de playlist para {len(analyzed_tracks)} pistas. Duración objetivo: {mix_duration_minutes} minutos.")

    if not analyzed_tracks:
        playlist_logger.warning("No hay pistas analizadas para generar una playlist.")
        return []

    # Filtrar pistas que tienen datos de análisis completos y válidos
    valid_tracks = [
        track for track in analyzed_tracks
        if all(k in track and track[k] is not None for k in ['bpm', 'camelot_key', 'energy', 'duration', 'filepath'])
        and track['duration'] > 0 # Asegurarse de que la duración no sea cero
        and os.path.exists(track['filepath']) # Asegurarse de que el archivo existe
    ]

    if not valid_tracks:
        playlist_logger.error("No hay pistas válidas con datos de análisis completos para generar la playlist.")
        return []

    playlist = []
    remaining_tracks = list(valid_tracks) # Copia mutable

    # Elegir una pista inicial aleatoria
    current_track = random.choice(remaining_tracks)
    playlist.append(current_track)
    remaining_tracks.remove(current_track)
    playlist_logger.info(f"Pista inicial seleccionada: {current_track['filename']} (BPM: {current_track['bpm']}, Key: {current_track['camelot_key']})")

    current_mix_duration_ms = current_track['duration'] * 1000 # Convertir a milisegundos
    target_mix_duration_ms = mix_duration_minutes * 60 * 1000

    while remaining_tracks and current_mix_duration_ms < target_mix_duration_ms:
        next_track = find_next_track(current_track, remaining_tracks)

        if next_track:
            playlist.append(next_track)
            remaining_tracks.remove(next_track)
            current_track = next_track
            current_mix_duration_ms += next_track['duration'] * 1000
            playlist_logger.info(f"Añadida a playlist: {next_track['filename']} (BPM: {next_track['bpm']}, Key: {next_track['camelot_key']}). Duración acumulada: {current_mix_duration_ms / 60000:.2f} min.")
        else:
            playlist_logger.warning("No se pudo encontrar una pista compatible para la siguiente transición. Terminando generación de playlist.")
            break
            
    # Si la playlist es demasiado corta, podemos intentar añadir más pistas aleatoriamente
    # o repetir pistas que ya están, aunque no sea lo ideal.
    # Por simplicidad, si no hay más pistas compatibles y la duración objetivo no se ha alcanzado,
    # simplemente terminamos. Una implementación más avanzada podría buscar pistas "menos compatibles"
    # como último recurso.

    playlist_logger.info(f"Generación de playlist finalizada. Número de pistas: {len(playlist)}. Duración total estimada: {current_mix_duration_ms / 60000:.2f} minutos.")
    return playlist

if __name__ == '__main__':
    # --- PRUEBA DE FUNCIONALIDAD ---
    playlist_logger.info("Iniciando prueba de playlist_generation.py")

    # Datos de análisis dummy para la prueba
    dummy_analysis_results = [
        {'filename': 'Track1.mp3', 'filepath': '/path/to/Track1.mp3', 'bpm': 120.0, 'key': 'C', 'camelot_key': '8B', 'energy': 0.7, 'duration': 180},
        {'filename': 'Track2.mp3', 'filepath': '/path/to/Track2.mp3', 'bpm': 122.0, 'key': 'G', 'camelot_key': '9B', 'energy': 0.75, 'duration': 200},
        {'filename': 'Track3.mp3', 'filepath': '/path/to/Track3.mp3', 'bpm': 120.0, 'key': 'F', 'camelot_key': '7B', 'energy': 0.68, 'duration': 190},
        {'filename': 'Track4.mp3', 'filepath': '/path/to/Track4.mp3', 'bpm': 125.0, 'key': 'Am', 'camelot_key': '8A', 'energy': 0.8, 'duration': 210},
        {'filename': 'Track5.mp3', 'filepath': '/path/to/Track5.mp3', 'bpm': 123.0, 'key': 'Em', 'camelot_key': '9A', 'energy': 0.72, 'duration': 220},
        {'filename': 'Track6.mp3', 'filepath': '/path/to/Track6.mp3', 'bpm': 118.0, 'key': 'C', 'camelot_key': '8B', 'energy': 0.65, 'duration': 170}, # Compatible con Track1
        {'filename': 'Track7.mp3', 'filepath': '/path/to/Track7.mp3', 'bpm': 121.0, 'key': 'Bb', 'camelot_key': '6B', 'energy': 0.78, 'duration': 230}, # Compatible con Track3
        {'filename': 'Track8.mp3', 'filepath': '/path/to/Track8.mp3', 'bpm': 120.5, 'key': 'F#m', 'camelot_key': '11A', 'energy': 0.71, 'duration': 195}, # Compatible con Track4
        # Añadir pistas que tienen BPMs muy diferentes para ver cómo afecta la puntuación
        {'filename': 'Track9.mp3', 'filepath': '/path/to/Track9.mp3', 'bpm': 100.0, 'key': 'C', 'camelot_key': '8B', 'energy': 0.5, 'duration': 150},
        {'filename': 'Track10.mp3', 'filepath': '/path/to/Track10.mp3', 'bpm': 140.0, 'key': 'C', 'camelot_key': '8B', 'energy': 0.9, 'duration': 160},
    ]

    # Para una prueba más realista, asegúrate de que 'filepath' realmente exista o Mockéalo.
    # Aquí, solo verificamos la lógica de la playlist.
    # En una aplicación real, los 'filepath' serían rutas a archivos de audio existentes.
    # Mockear la existencia de archivos para la prueba
    for track in dummy_analysis_results:
        track['filepath'] = os.path.join(tempfile.gettempdir(), track['filename'])
        # Para la prueba, creamos archivos dummy muy pequeños
        with open(track['filepath'], 'w') as f:
            f.write("dummy audio data")
    
    playlist_logger.info("Generando lista de reproducción de 5 minutos:")
    test_playlist = generate_playlist(dummy_analysis_results, mix_duration_minutes=5)
    
    if test_playlist:
        playlist_logger.info("Playlist generada:")
        for i, track in enumerate(test_playlist):
            playlist_logger.info(f"{i+1}. {track['filename']} (BPM: {track['bpm']}, Key: {track['camelot_key']}, Energy: {track['energy']:.2f}, Duration: {track['duration']}s)")
    else:
        playlist_logger.warning("No se pudo generar la playlist de prueba.")

    # Prueba con datos vacíos
    playlist_logger.info("\nGenerando lista de reproducción con datos vacíos:")
    empty_playlist = generate_playlist([])
    playlist_logger.info(f"Resultado de playlist vacía: {empty_playlist}")

    # Prueba con claves que podrían no mapear o datos incompletos
    dummy_analysis_results_bad_data = [
        {'filename': 'TrackX.mp3', 'filepath': '/path/to/TrackX.mp3', 'bpm': 120.0, 'key': 'UnknownKey', 'energy': 0.7, 'duration': 180},
        {'filename': 'TrackY.mp3', 'filepath': '/path/to/TrackY.mp3', 'bpm': None, 'key': 'Cmaj', 'camelot_key': '8B', 'energy': 0.8, 'duration': 190},
        {'filename': 'TrackZ.mp3', 'filepath': '/path/to/TrackZ.mp3', 'bpm': 125.0, 'key': 'Dmin', 'camelot_key': '7A', 'energy': None, 'duration': 200},
        {'filename': 'TrackW.mp3', 'filepath': None, 'bpm': 125.0, 'key': 'Dmin', 'camelot_key': '7A', 'energy': 0.9, 'duration': 200}, # Falta filepath
    ]
    # Mockear la existencia de archivos para la prueba de datos malos
    for track in dummy_analysis_results_bad_data:
        if track['filepath']: # Solo si filepath no es None
            with open(track['filepath'], 'w') as f:
                f.write("dummy audio data")

    playlist_logger.info("\nGenerando lista de reproducción con algunos datos incompletos/malos:")
    bad_data_playlist = generate_playlist(dummy_analysis_results_bad_data)
    playlist_logger.info(f"Resultado de playlist con datos malos: {len(bad_data_playlist)} pistas")


    # Limpiar archivos dummy creados para la prueba
    for track in dummy_analysis_results:
        if os.path.exists(track['filepath']):
            os.remove(track['filepath'])
    for track in dummy_analysis_results_bad_data:
        if track['filepath'] and os.path.exists(track['filepath']):
            os.remove(track['filepath'])
    
    playlist_logger.info("Archivos de prueba limpiados.")
    playlist_logger.info("Prueba de playlist_generation.py finalizada.")