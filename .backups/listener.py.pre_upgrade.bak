import speech_recognition as sr
from modules.logger import log_error, log_info

def listen_to_user(hud_callback=None) -> str:
    """Captures microphone input and converts it to text safely."""
    recognizer = sr.Recognizer()
    
    try:
        with sr.Microphone() as source:
            log_info("Calibrating microphone for ambient noise...")
            if hud_callback:
                hud_callback("CALIBRATING MIC...", "listening")
            
            recognizer.adjust_for_ambient_noise(source, duration=0.8)
            
            log_info("Listening for command...")
            if hud_callback:
                hud_callback("LISTENING // SPEAK NOW", "listening")
            
            # Listen with a timeout so it doesn't freeze permanently
            audio = recognizer.listen(source, timeout=6, phrase_time_limit=10)
            
            if hud_callback:
                hud_callback("PROCESSING SPEECH...", "thinking")
                
            query = recognizer.recognize_google(audio)
            log_info(f"Recognized speech: {query}")
            return query
            
    except sr.WaitTimeoutError:
        log_info("Listening timed out. No speech detected.")
        return ""
    except sr.UnknownValueError:
        log_info("Could not understand audio.")
        return ""
    except Exception as e:
        log_error(f"Microphone error: {e}")
        return ""