import xml.etree.ElementTree as ET
import os
import sys
import urllib.parse
import logging
import re # Para expresiones regulares en la limpieza de URL
import xmltodict # Para parsear XML a diccionario de forma más sencilla
import tempfile
from pydub import AudioSegment # Usado solo para generar MP3s dummy en __main__

# Configuración del logger para apple_music_integration
apple_logger = logging.getLogger('apple_music_integration_module')
apple_logger.setLevel(logging.DEBUG)
handler = logging.StreamHandler(sys.stderr)
formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
handler.setFormatter(formatter)
apple_logger.addHandler(handler)

# --- INICIO DE LA MODIFICACIÓN (Añadir estas líneas para asegurar FFmpeg para pydub) ---
# Establecer la ruta a los ejecutables de FFmpeg para pydub en este módulo también,
# en caso de que se use para operaciones de audio (ej. en pruebas)
try:
    AudioSegment.converter = "/usr/local/bin/ffmpeg"
    AudioSegment.ffmpeg = "/usr/local/bin/ffmpeg"
    AudioSegment.ffprobe = "/usr/local/bin/ffprobe"
except Exception as e:
    apple_logger.warning(f"No se pudieron configurar las rutas de FFmpeg para pydub en apple_music_integration.py: {e}. Esto puede ser un problema si este módulo genera o procesa audio.")
# --- FIN DE LA MODIFICACIÓN ---


def normalize_filepath(location_url: str) -> str:
    """
    Normaliza una URL de localización de archivo de iTunes a una ruta de sistema de archivos local.
    Maneja el esquema 'file:///' y la decodificación de URL.
    """
    if not location_url:
        return ""

    try:
        # Decodificar la URL
        decoded_url = urllib.parse.unquote(location_url)
        
        # Eliminar el esquema 'file:///' o 'file://localhost/'
        if decoded_url.startswith('file:///'):
            path = decoded_url[len('file://'):] # Conservar un '/' para rutas absolutas
        elif decoded_url.startswith('file://localhost/'):
            path = decoded_url[len('file://localhost'):]
        else:
            path = decoded_url # Si no tiene esquema file://, asumir que ya es una ruta

        # En Windows, convertir las barras si es necesario y manejar la unidad
        if sys.platform == "win32":
            # Si la ruta comienza con una barra y una letra de unidad (ej. /C:/), quitar la barra inicial
            if re.match(r'^/[a-zA-Z]:/', path):
                path = path[1:]
            path = path.replace('/', '\\') # Convertir barras a contrabarras

        return path
    except Exception as e:
        apple_logger.error(f"Error normalizando la ruta de archivo '{location_url}': {e}")
        return ""

def parse_itunes_xml(xml_filepath: str) -> list:
    """
    Parsea un archivo XML de la biblioteca de iTunes/Apple Music y extrae información de las pistas.
    Retorna una lista de diccionarios, cada uno representando una pista.
    """
    apple_logger.info(f"Parseando archivo XML de iTunes: {xml_filepath}")
    tracks_data = []

    if not os.path.exists(xml_filepath):
        apple_logger.error(f"Archivo XML no encontrado: {xml_filepath}")
        return []

    try:
        # Usar xmltodict para una conversión más fácil a diccionario
        with open(xml_filepath, 'r', encoding='utf-8') as f:
            xml_dict = xmltodict.parse(f.read())

        # Navegar a la sección de pistas (Track)
        # La estructura suele ser plist -> dict -> dict (Tracks)
        tracks_dict = xml_dict.get('plist', {}).get('dict', {}).get('dict', {}).get('Tracks', {})

        for track_id, track_info in tracks_dict.items():
            track = {
                'Track ID': track_info.get('Track ID'),
                'Name': track_info.get('Name'),
                'Artist': track_info.get('Artist'),
                'Album': track_info.get('Album'),
                'Genre': track_info.get('Genre'),
                'Kind': track_info.get('Kind'),
                'Size': track_info.get('Size'),
                'Total Time': track_info.get('Total Time'), # en milisegundos
                'Disc Number': track_info.get('Disc Number'),
                'Disc Count': track_info.get('Disc Count'),
                'Track Number': track_info.get('Track Number'),
                'Track Count': track_info.get('Track Count'),
                'Year': track_info.get('Year'),
                'Date Modified': track_info.get('Date Modified'),
                'Date Added': track_info.get('Date Added'),
                'Bit Rate': track_info.get('Bit Rate'),
                'Sample Rate': track_info.get('Sample Rate'),
                'Play Count': track_info.get('Play Count'),
                'Play Date UTC': track_info.get('Play Date UTC'),
                'Artwork Count': track_info.get('Artwork Count'),
                'Persistent ID': track_info.get('Persistent ID'),
                'Track Type': track_info.get('Track Type'),
                'Location': normalize_filepath(track_info.get('Location')) # Normalizar la ruta del archivo
            }
            tracks_data.append(track)
            apple_logger.debug(f"Pista encontrada: {track.get('Name')} - {track.get('Artist')} ({track.get('Location')})")

    except ET.ParseError as pe:
        apple_logger.error(f"Error de parseo XML en '{xml_filepath}': {pe}")
    except Exception as e:
        apple_logger.error(f"Error inesperado al leer XML de iTunes '{xml_filepath}': {e}", exc_info=True)
    
    apple_logger.info(f"Se encontraron {len(tracks_data)} pistas en el XML.")
    return tracks_data

if __name__ == '__main__':
    # --- PRUEBA DE FUNCIONALIDAD ---
    apple_logger.info("Iniciando prueba de apple_music_integration.py")

    # 1. Crear un archivo XML de iTunes dummy para la prueba
    test_output_dir = tempfile.mkdtemp(prefix='itunes_xml_test_')
    test_xml_file = os.path.join(test_output_dir, 'iTunes_Library_Test.xml')
    dummy_music_dir = os.path.join(test_output_dir, 'dummy_music_files')
    os.makedirs(dummy_music_dir, exist_ok=True)

    # Crear archivos MP3 dummy para que las rutas en el XML existan
    dummy_mp3_path1 = os.path.join(dummy_music_dir, 'test_song_1.mp3')
    dummy_mp3_path2 = os.path.join(dummy_music_dir, 'test_song_2.mp3')

    try:
        # Usar pydub para crear un MP3 válido, si ffmpeg está configurado
        AudioSegment.silent(duration=5000).export(dummy_mp3_path1, format="mp3")
        AudioSegment.silent(duration=7000).export(dummy_mp3_path2, format="mp3")
        apple_logger.info("Archivos MP3 dummy creados.")
    except Exception as e:
        apple_logger.warning(f"No se pudieron crear los archivos MP3 dummy (requiere FFmpeg en PATH para pydub): {e}")
        # Continuar la prueba sin MP3s reales, pero las rutas no serán válidas.

    # Generar contenido XML dummy
    xml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple Computer//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Major Version</key><integer>1</integer>
    <key>Minor Version</key><integer>1</integer>
    <key>Application Version</key><string>12.10.10.2</string>
    <key>Features</key><integer>5</integer>
    <key>Show Content Ratings</key><true/>
    <key>Music Folder</key><string>file:///Users/Shared/Music/iTunes/iTunes%20Media/</string>
    <key>Library Persistent ID</key><string>XXXXX</string>
    <key>Tracks</key>
    <dict>
        <key>1234</key>
        <dict>
            <key>Track ID</key><integer>1234</integer>
            <key>Name</key><string>Test Song 1</string>
            <key>Artist</key><string>Artist A</string>
            <key>Album</key><string>Album X</string>
            <key>Kind</key><string>MPEG audio file</string>
            <key>Size</key><integer>1234567</integer>
            <key>Total Time</key><integer>5000</integer>
            <key>Date Modified</key><date>2023-01-01T00:00:00Z</date>
            <key>Date Added</key><date>2023-01-01T00:00:00Z</date>
            <key>Bit Rate</key><integer>192</integer>
            <key>Sample Rate</key><integer>44100</integer>
            <key>Play Count</key><integer>1</integer>
            <key>Play Date UTC</key><date>2023-01-02T00:00:00Z</date>
            <key>Persistent ID</key><string>YYYYY</string>
            <key>Track Type</key><string>File</string>
            <key>Location</key><string>file://{urllib.parse.quote(dummy_mp3_path1)}</string>
        </dict>
        <key>5678</key>
        <dict>
            <key>Track ID</key><integer>5678</integer>
            <key>Name</key><string>Test Song 2</string>
            <key>Artist</key><string>Artist B</string>
            <key>Album</key><string>Album Y</string>
            <key>Kind</key><string>MPEG audio file</string>
            <key>Size</key><integer>9876543</integer>
            <key>Total Time</key><integer>7000</integer>
            <key>Date Modified</key><date>2023-02-01T00:00:00Z</date>
            <key>Date Added</key><date>2023-02-01T00:00:00Z</date>
            <key>Bit Rate</key><integer>192</integer>
            <key>Sample Rate</key><integer>44100</integer>
            <key>Play Count</key><integer>5</integer>
            <key>Play Date UTC</key><date>2023-02-05T00:00:00Z</date>
            <key>Persistent ID</key><string>ZZZZZ</string>
            <key>Track Type</key><string>File</string>
            <key>Location</key><string>file://{urllib.parse.quote(dummy_mp3_path2)}</string>
        </dict>
        <key>9999</key>
        <dict>
            <key>Track ID</key><integer>9999</integer>
            <key>Name</key><string>Not an MP3</string>
            <key>Artist</key><string>Artist C</string>
            <key>Kind</key><string>MPEG-4 video file</string>
            <key>Location</key><string>file:///path/to/video.mp4</string>
        </dict>
    </dict>
    <key>Playlists</key>
    <array>
        <dict>
            <key>Playlist ID</key><integer>1</integer>
            <key>Name</key><string>Library</string>
            <key>Playlist Items</key>
            <array>
                <dict><key>Track ID</key><integer>1234</integer></dict>
                <dict><key>Track ID</key><integer>5678</integer></dict>
            </array>
        </dict>
    </array>
</dict>
</plist>
"""
    # Escribir el XML en el archivo dummy
    with open(test_xml_file, 'w', encoding='utf-8') as f:
        f.write(xml_content)
    apple_logger.info(f"Archivo XML de prueba generado en: {test_xml_file}")

    # 2. Parsear el archivo XML dummy
    parsed_tracks = parse_itunes_xml(test_xml_file)

    apple_logger.info("\n--- Pistas Parseadas del XML de Prueba ---")
    if parsed_tracks:
        for track in parsed_tracks:
            apple_logger.info(f"  Name: {track.get('Name')}, Artist: {track.get('Artist')}, Location: {track.get('Location')}")
    else:
        apple_logger.error(f"La prueba de parseo del archivo '{test_xml_file}' no encontró pistas o hubo un error.")
        apple_logger.error("Por favor, exporta tu biblioteca de iTunes/Apple Music a un archivo XML o asegúrate de que el dummy se haya creado correctamente.")
    
    # Limpiar archivo dummy y los MP3 dummies
    if os.path.exists(test_xml_file):
        try:
            os.remove(test_xml_file)
            apple_logger.info("Archivo XML de prueba limpiado.")
        except Exception as e:
            apple_logger.warning(f"No se pudo eliminar el archivo de prueba {test_xml_file}: {e}")
    
    # Limpiar MP3 dummies
    dummy_mp3_paths = [
        dummy_mp3_path1,
        dummy_mp3_path2
    ]
    for mp3_path in dummy_mp3_paths:
        if os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
                apple_logger.info(f"Archivo MP3 dummy limpiado: {mp3_path}")
            except Exception as e:
                apple_logger.warning(f"No se pudo eliminar el archivo MP3 dummy {mp3_path}: {e}")

    # Intentar limpiar el directorio 'dummy_music_files' si está vacío
    if os.path.exists(dummy_music_dir) and not os.listdir(dummy_music_dir):
        try:
            os.rmdir(dummy_music_dir)
            apple_logger.info(f"Directorio dummy_music_files limpiado: {dummy_music_dir}")
        except OSError as e:
            apple_logger.warning(f"No se pudo eliminar el directorio dummy_music_files {dummy_music_dir}: {e}")
    
    # Limpiar el directorio temporal principal
    if os.path.exists(test_output_dir):
        try:
            os.rmdir(test_output_dir)
            apple_logger.info(f"Directorio temporal de prueba limpiado: {test_output_dir}")
        except OSError as e:
            apple_logger.warning(f"No se pudo eliminar el directorio temporal {test_output_dir}: {e}")


    apple_logger.info("Prueba de apple_music_integration.py finalizada.")