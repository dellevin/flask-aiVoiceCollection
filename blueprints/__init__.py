# blueprints package
from .pin_tu import bp as pin_tu_bp
from .base64_codec import bp as base64_bp
from .down_video import bp as down_video_bp
from .fen_ci import bp as fen_ci_bp
from .content_tag import bp as content_tag_bp
from .chmod_calc import bp as chmod_calc_bp
from .json_format import bp as json_format_bp
from .qr_code import bp as qr_code_bp
from .http_status import bp as http_status_bp
from .url_parser import bp as url_parser_bp
from .token_gen import bp as token_gen_bp
from .sovits_tts import bp as sovits_tts_bp
from .stt import bp as stt_bp
from .ai_dubbing import bp as ai_dubbing_bp
from .rvc import bp as rvc_bp
from .audio_slicer import bp as audio_slicer_bp
from .uvr_sep import bp as uvr_sep_bp
from .mp4_to_audio import bp as mp4_to_audio_bp

__all__ = [
    'pin_tu_bp', 'base64_bp', 'down_video_bp', 'fen_ci_bp', 'content_tag_bp',
    'chmod_calc_bp', 'json_format_bp', 'qr_code_bp', 'http_status_bp', 'url_parser_bp', 'token_gen_bp',
    'sovits_tts_bp', 'stt_bp', 'ai_dubbing_bp', 'rvc_bp', 'audio_slicer_bp', 'uvr_sep_bp', 'mp4_to_audio_bp',
]
