import subprocess
from pathlib import Path

from .errors import WorkflowError
from .files import file_digest


def verify_video(path):
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise WorkflowError("缺影片解碼工具。", "waiting_capability") from exc
    import subprocess
    result = subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), "-v", "error", "-i", str(path), "-f", "null", "-"], capture_output=True, timeout=90)
    if result.returncode or not Path(path).is_file() or Path(path).stat().st_size == 0:
        raise WorkflowError("影片未通過實際解碼。", "invalid_asset")
    frames=imageio_ffmpeg.read_frames(str(path),output_params=['-frames:v','1'])
    try:
        metadata=next(frames);frame=next(frames)
        width,height=metadata['size']
        if len(frame)!=width*height*3:raise WorkflowError('影片畫面尺寸與解碼不符。','invalid_asset')
    except (RuntimeError,StopIteration,KeyError) as exc:
        raise WorkflowError('影片沒有可讀的實際畫面。','invalid_asset') from exc
    finally:frames.close()
    return {"path": str(Path(path).resolve()), "sha256": file_digest(path), "decoded": True,
        'width':width,'height':height,'duration_seconds':metadata.get('duration') or None,'fps':metadata.get('fps') or None}


def make_video(image, output, duration=6, size=(1080,1920)):
    """Render an original faceless graphic video; not a fabricated product demo."""
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise WorkflowError("缺影片編碼工具；安裝套件的影片依賴後接續。", "waiting_capability") from exc
    if type(duration) not in (int,float) or not 1 <= duration <= 30:
        raise ValueError("Graphic video duration must be between 1 and 30 seconds")
    if len(size)!=2 or any(type(v) is not int or not 64<=v<=2160 or v%2 for v in size):
        raise ValueError('Video dimensions must be even integers from 64 to 2160')
    from .media import verify_image
    verify_image(image)
    path = Path(output)
    path.parent.mkdir(parents=True, exist_ok=True)
    encoder = imageio_ffmpeg.get_ffmpeg_exe()
    command = [encoder, "-y", "-loop", "1", "-i", str(image), "-t", str(duration),
               "-r", "24", "-vf", "scale=%d:%d:force_original_aspect_ratio=decrease,pad=%d:%d:(ow-iw)/2:(oh-ih)/2:color=white,setsar=1,format=yuv420p"%(size[0],size[1],size[0],size[1]), "-c:v", "libx264",
               "-preset", "fast", "-movflags", "+faststart", "-an", str(path)]
    result = subprocess.run(command, capture_output=True, timeout=90)
    if result.returncode != 0 or not path.exists():
        raise WorkflowError("影片編碼失敗；素材原檔保留。", "invalid_asset")
    # A second real decoder invocation verifies that the exported media is usable.
    decoded=verify_video(path)
    if (decoded['width'],decoded['height'])!=size:raise WorkflowError('影片輸出尺寸不同。','invalid_asset')
    return {**decoded, "bytes": path.stat().st_size,"requested_duration_seconds":duration,"audio":False,
            "production": "original graphic video, not a recorded product demonstration"}
