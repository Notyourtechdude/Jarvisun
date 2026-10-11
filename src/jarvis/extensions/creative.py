"""Standalone website artifacts, bounded silent video cuts, and an optional image engine."""

import html
import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from .common import ExtensionError, bounded_text, output_file, regular_file


def write_website(path, *, title, description, metrics, allow_cdn=False):
    """Template business page with native projected 3D; optional Three.js CDN enhancement."""
    title = html.escape(bounded_text(title, 100))
    description = html.escape(bounded_text(description, 1000))
    if not isinstance(metrics, dict) or len(metrics) > 12:
        raise ExtensionError('Supply at most twelve business metrics')
    cards = ''.join('<article><span>' + html.escape(str(k)[:100]) + '</span><strong>'
                    + html.escape(str(v)[:200]) + '</strong></article>' for k, v in metrics.items())
    cdn = '''
// Optional external enhancement; the native canvas and all controls remain the fallback.
(async () => {
try {
 const THREE = await import('https://cdn.jsdelivr.net/npm/three@0.180.0/build/three.module.js');
 const renderer = new THREE.WebGLRenderer({alpha:true,antialias:true});
 renderer.setPixelRatio(ratio); renderer.setSize(width,height);
 renderer.domElement.className='three-enhancement'; renderer.domElement.setAttribute('aria-hidden','true');
 renderer.domElement.style.pointerEvents='none'; host.append(renderer.domElement);
 const scene = new THREE.Scene(), camera = new THREE.PerspectiveCamera(45,320/260,.1,100);
 camera.position.z=5;
 const mesh = new THREE.Mesh(new THREE.IcosahedronGeometry(1.35,1),
   new THREE.MeshNormalMaterial({wireframe:true})); scene.add(mesh);
 enhancement=()=>{mesh.rotation.x=rotation.x;mesh.rotation.y=rotation.y;renderer.render(scene,camera);};
 draw();
} catch { document.querySelector('.notice').textContent='External CDN unavailable; native 3D remains active.'; }
})();''' if allow_cdn else ''
    native = '''<script>
(() => {
'use strict';
const canvas=document.querySelector('#scene-canvas'), host=document.querySelector('#scene');
const context=canvas.getContext('2d');
if(!context)return;
document.querySelector('.orb').hidden=true;
const width=320,height=260,ratio=Math.min(window.devicePixelRatio||1,2);
canvas.width=width*ratio;canvas.height=height*ratio;
const preference=matchMedia('(prefers-reduced-motion:reduce)');
const initial={x:.35,y:.55},rotation={...initial};
let running=!preference.matches,drag=null,previous=0,enhancement=null;
const phi=(1+Math.sqrt(5))/2;
const vertices=[[-1,phi,0],[1,phi,0],[-1,-phi,0],[1,-phi,0],
 [0,-1,phi],[0,1,phi],[0,-1,-phi],[0,1,-phi],[phi,0,-1],[phi,0,1],[-phi,0,-1],[-phi,0,1]];
const faces=[[0,11,5],[0,5,1],[0,1,7],[0,7,10],[0,10,11],[1,5,9],[5,11,4],
 [11,10,2],[10,7,6],[7,1,8],[3,9,4],[3,4,2],[3,2,6],[3,6,8],[3,8,9],
 [4,9,5],[2,4,11],[6,2,10],[8,6,7],[9,8,1]];
function project(point){
 const [px,py,pz]=point,cx=Math.cos(rotation.x),sx=Math.sin(rotation.x),
 cy=Math.cos(rotation.y),sy=Math.sin(rotation.y);
 const y=py*cx-pz*sx,z=py*sx+pz*cx,x=px*cy+z*sy,depth=-px*sy+z*cy;
 const perspective=170/(5-depth);
 return {x:width/2+x*perspective,y:height/2+y*perspective,z:depth};
}
function draw(){
 context.setTransform(ratio,0,0,ratio,0,0);context.clearRect(0,0,width,height);
 const points=vertices.map(project);
 const ordered=faces.map(face=>({face,depth:face.reduce((sum,index)=>sum+points[index].z,0)/3}));
 ordered.sort((a,b)=>a.depth-b.depth);
 for(const {face,depth} of ordered){
  const light=Math.round(95+(depth+2)*20);
  context.beginPath();face.forEach((index,position)=>{
   const point=points[index];if(position===0)context.moveTo(point.x,point.y);else context.lineTo(point.x,point.y);
  });context.closePath();context.fillStyle=`rgba(30,${light},115,.30)`;context.fill();
  context.strokeStyle=`rgba(155,236,210,${.3+(depth+2)/7})`;context.lineWidth=1.25;context.stroke();
 }
 for(const point of points){context.beginPath();context.arc(point.x,point.y,2.3,0,Math.PI*2);
  context.fillStyle='#d2fff0';context.fill();}
 if(enhancement){context.clearRect(0,0,width,height);enhancement();}
}
const toggle=document.querySelector('#motion');
function state(){toggle.textContent=running?'Pause motion':'Resume motion';
 toggle.setAttribute('aria-pressed',String(!running));}
function rotate(x,y){rotation.x=Math.max(-1.4,Math.min(1.4,rotation.x+x));
 rotation.y=(rotation.y+y)%(Math.PI*2);draw();}
function manual(x,y){running=false;state();rotate(x,y);}
document.querySelectorAll('.scene-controls button').forEach(button=>{button.disabled=false;});
toggle.onclick=()=>{running=!running;previous=0;state();};
document.querySelector('#reset-view').onclick=()=>{Object.assign(rotation,initial);draw();};
document.querySelector('#rotate-left').onclick=()=>manual(0,-.15);
document.querySelector('#rotate-right').onclick=()=>manual(0,.15);
document.querySelector('#rotate-up').onclick=()=>manual(-.15,0);
document.querySelector('#rotate-down').onclick=()=>manual(.15,0);
canvas.addEventListener('keydown',event=>{
 const moves={ArrowLeft:[0,-.15],ArrowRight:[0,.15],ArrowUp:[-.15,0],ArrowDown:[.15,0]};
 if(moves[event.key]){event.preventDefault();manual(...moves[event.key]);}
});
canvas.addEventListener('pointerdown',event=>{
 if(event.button!==0)return;running=false;state();
 drag={id:event.pointerId,x:event.clientX,y:event.clientY};canvas.setPointerCapture(event.pointerId);
});
canvas.addEventListener('pointermove',event=>{
 if(!drag||drag.id!==event.pointerId)return;
 rotate((event.clientY-drag.y)*.012,(event.clientX-drag.x)*.012);
 drag.x=event.clientX;drag.y=event.clientY;
});
function finish(event){if(drag&&drag.id===event.pointerId)drag=null;}
canvas.addEventListener('pointerup',finish);canvas.addEventListener('pointercancel',finish);
canvas.addEventListener('lostpointercapture',finish);
preference.addEventListener('change',event=>{if(event.matches){running=false;state();}});
function tick(now){const elapsed=previous?Math.min((now-previous)/1000,.05):0;previous=now;
 if(running&&!document.hidden&&!drag)rotate(0,elapsed*.28);requestAnimationFrame(tick);}
state();draw();requestAnimationFrame(tick);
''' + cdn + '\n})();</script>'
    notice = ('<p class="notice">External CDN enabled: Three.js downloads from jsDelivr when opened.</p>'
              if allow_cdn else '')
    document = '''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>''' + title + '''</title>
<style>
:root{color-scheme:dark;font-family:system-ui,sans-serif;background:#111722;color:#f8fafc}
body{max-width:1000px;margin:0 auto;padding:clamp(24px,6vw,72px)}
.eyebrow{font-size:.8rem;letter-spacing:.2em;color:#9becd2}h1{font-size:clamp(2.7rem,8vw,6rem);line-height:1.02;max-width:12ch}
p{max-width:60ch;line-height:1.6;color:#bac6d4;overflow-wrap:anywhere}.hero{display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.copy{flex:1;min-width:min(240px,100%)}.visual{width:320px;max-width:100%}#scene{position:relative;width:100%;aspect-ratio:320/260;display:grid;place-items:center}
#scene-canvas,.three-enhancement{position:absolute;width:100%;height:100%;max-width:100%;touch-action:none}
#scene-canvas{cursor:grab}#scene-canvas:active{cursor:grabbing}.three-enhancement{inset:0}
.scene-controls{display:flex;flex-wrap:wrap;gap:8px}.scene-controls button{padding:10px 12px}
.hint{font-size:.8rem}.metrics strong,.metrics span,h1{overflow-wrap:anywhere}
.orb{width:170px;height:170px;border:2px solid #9becd2;transform:rotate(30deg);border-radius:35%;box-shadow:0 0 70px #79dcb033;animation:turn 18s linear infinite}
.orb:before,.orb:after{content:'';position:absolute;inset:20px;border:1px solid #9becd2;border-radius:35%;transform:rotate(45deg)}
.orb:after{inset:40px;transform:rotate(90deg)}@keyframes turn{to{transform:rotate(390deg)}}
.metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px;margin-top:36px}
article{border-top:1px solid #435366;padding:24px 8px}article span{display:block;color:#bac6d4}strong{display:block;font-size:2rem;margin-top:12px}
button{background:#9becd2;color:#111722;padding:12px 20px;border:0;border-radius:4px;cursor:pointer;font-weight:700}
button:focus-visible,canvas:focus-visible{outline:3px solid white;outline-offset:4px}.notice{font-size:.8rem}
@media(prefers-reduced-motion:reduce){.orb{animation:none}}
</style><main><p class="eyebrow">BUSINESS SNAPSHOT</p><section class="hero"><div class="copy"><h1>''' + title + '''</h1><p>''' + description + '''</p></div>
<div class="visual"><div id="scene"><div class="orb" aria-hidden="true"></div>
<canvas id="scene-canvas" width="320" height="260" tabindex="0" role="img"
 aria-label="Interactive projected 3D geometry" aria-describedby="scene-help">Static business visualization.</canvas></div>
<p id="scene-help" class="hint">Drag or use arrow keys to rotate the 3D geometry.</p>
<div class="scene-controls" role="group" aria-label="3D visualization controls">
<button id="motion" disabled aria-pressed="false">Pause motion</button><button id="reset-view" disabled>Reset view</button>
<button id="rotate-left" disabled>Rotate left</button><button id="rotate-right" disabled>Rotate right</button>
<button id="rotate-up" disabled>Rotate up</button><button id="rotate-down" disabled>Rotate down</button></div>
<noscript><p class="hint">JavaScript is disabled; the static visual and business metrics remain available.</p></noscript>
''' + notice + '''</div></section>
<section class="metrics" aria-label="Business metrics">''' + cards + '''</section></main>
''' + native + '</html>'
    path = output_file(path)
    if path.suffix.lower() != '.html':
        raise ExtensionError('Website output must use an HTML extension')
    path.write_text(document, encoding='utf-8')
    return {'artifact': str(path), 'kind': 'website', 'external_cdn': bool(allow_cdn),
            'business_metrics': len(metrics)}


def _times(start, duration):
    if (not isinstance(start, (int, float)) or not isinstance(duration, (int, float))
            or not math.isfinite(start) or not math.isfinite(duration)
            or not 0 <= start <= 120 or not 0 < duration <= 30):
        raise ExtensionError('Video start must be 0–120 seconds and duration must be 0–30 seconds')


def video_command(source, destination, *, start=0, duration=10, captions=False):
    _times(start, duration)
    source, destination = Path(source).absolute(), Path(destination).absolute()
    if source.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
        raise ExtensionError('Video input must be MP4, MOV, MKV, or WebM')
    if destination.suffix.lower() != '.mp4':
        raise ExtensionError('Video output must be MP4')
    filters = 'scale=640:360:force_original_aspect_ratio=decrease,pad=640:360:(ow-iw)/2:(oh-ih)/2,fps=24'
    if captions:
        filters += ',subtitles=captions.srt'
    return ['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y', '-max_alloc', '67108864',
            '-threads', '1', '-filter_threads', '1', '-protocol_whitelist', 'file',
            '-format_whitelist', 'mov,matroska,webm',
            '-ss', str(start), '-i', str(source), '-t', str(duration), '-map', '0:v:0', '-an',
            '-vf', filters, '-c:v', 'libx264', '-threads', '1', '-preset', 'fast', '-crf', '23',
            '-pix_fmt', 'yuv420p', '-map_metadata', '-1', '-fflags', '+bitexact',
            '-flags:v', '+bitexact', '-movflags', '+faststart', str(destination)]


def _run(args, *, timeout, cwd=None, input_bytes=None):
    try:
        # File-backed diagnostics keep hostile decoder output out of process memory.
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            completed = subprocess.run(args, cwd=cwd, shell=False,
                                       stdin=subprocess.DEVNULL if input_bytes is None else None,
                                       input=input_bytes, stdout=stdout, stderr=stderr,
                                       timeout=timeout, check=True)
            stdout.seek(0)
            completed.stdout = stdout.read(131073)
            if len(completed.stdout) > 131072:
                raise ExtensionError('Local engine response exceeds its output limit')
            return completed
    except (OSError, subprocess.SubprocessError):
        raise ExtensionError('Local engine failed or exceeded its time limit') from None


def _probe(source):
    result = _run(['ffprobe', '-v', 'error', '-protocol_whitelist', 'file', '-max_alloc', '67108864',
                   '-format_whitelist', 'mov,matroska,webm',
                   '-show_entries', 'stream=codec_type,codec_name,width,height:format=duration',
                   '-of', 'json', str(source)], timeout=10)
    try:
        info = json.loads(result.stdout)
        streams = [s for s in info['streams'] if s['codec_type'] == 'video']
        stream = streams[0]
        length = float(info['format']['duration'])
        if (stream['codec_name'] not in {'h264', 'mpeg4', 'vp8', 'vp9', 'av1'}
                or not 0 < stream['width'] <= 1920 or not 0 < stream['height'] <= 1080
                or not math.isfinite(length) or not 0 < length <= 120):
            raise ValueError
        return length
    except (ValueError, KeyError, IndexError, TypeError):
        raise ExtensionError('Video codec, dimensions, or duration exceeds supported limits') from None


def _stamp(value):
    milliseconds = round(value * 1000)
    hours, milliseconds = divmod(milliseconds, 3600000)
    minutes, milliseconds = divmod(milliseconds, 60000)
    seconds, milliseconds = divmod(milliseconds, 1000)
    return f'{hours:02}:{minutes:02}:{seconds:02},{milliseconds:03}'


def render_video(source, destination, *, start=0, duration=10, captions=()):
    """Render one deterministic silent cut; caption times are relative to the cut."""
    _times(start, duration)
    source, destination = regular_file(source, 100_000_000).absolute(), output_file(destination).absolute()
    if source.suffix.lower() not in {'.mp4', '.mov', '.mkv', '.webm'}:
        raise ExtensionError('Video input must be MP4, MOV, MKV, or WebM')
    if source == destination or len(captions) > 20:
        raise ExtensionError('Use a distinct video output and at most twenty captions')
    if not shutil.which('ffmpeg') or not shutil.which('ffprobe'):
        raise ExtensionError('Video rendering requires FFmpeg and FFprobe')
    length = _probe(source)
    if start + duration > length + .05:
        raise ExtensionError('Requested cut extends beyond the source video')
    subtitles = []
    for index, caption in enumerate(captions, 1):
        if not isinstance(caption, (tuple, list)) or len(caption) != 3:
            raise ExtensionError('Caption must contain start, end, and text')
        begin, end, text = caption
        if (not isinstance(begin, (int, float)) or not isinstance(end, (int, float))
                or not 0 <= begin < end <= duration):
            raise ExtensionError('Caption timing must fall within the cut')
        text = bounded_text(text, 200).replace('\n', ' ').replace('\r', ' ')
        for char in '{}<>\\':
            text = text.replace(char, '')
        subtitles.append(f'{index}\n{_stamp(begin)} --> {_stamp(end)}\n{text}\n')
    with tempfile.TemporaryDirectory(prefix='jarvis-video-') as temp:
        Path(temp, 'captions.srt').write_text('\n'.join(subtitles), encoding='utf-8')
        staged = Path(temp, 'render.mp4')
        _run(video_command(source, staged, start=start, duration=duration, captions=bool(captions)),
             timeout=60, cwd=temp)
        regular_file(staged, 20_000_000)
        verified_duration = _probe(staged)
        with staged.open('rb') as src, destination.open('wb') as dst:
            shutil.copyfileobj(src, dst)
    return {'artifact': str(destination), 'kind': 'video', 'duration': verified_duration,
            'audio': False, 'captions': len(captions)}


class ImageGenerator:
    """Optional owner-supplied local wrapper contract; no bundled model or synthetic result."""

    def __init__(self, executable=None):
        if executable and not Path(executable).is_absolute():
            raise ExtensionError('Image engine must use an absolute executable path')
        self.executable = Path(executable) if executable else None

    def available(self):
        enabled = bool(self.executable and self.executable.is_file() and not self.executable.is_symlink()
                       and os.access(self.executable, os.X_OK))
        return {'available': enabled, 'reason': 'local image wrapper configured' if enabled
                else 'No local image engine configured', 'live_verified': False}

    def generate(self, prompt, destination):
        if not self.available()['available']:
            raise ExtensionError('No local image engine configured')
        prompt, destination = bounded_text(prompt, 2000), output_file(destination)
        if destination.suffix.lower() != '.png':
            raise ExtensionError('Image output must be PNG')
        with tempfile.TemporaryDirectory(prefix='jarvis-image-') as temp:
            staged = Path(temp, 'image.png')
            _run([str(self.executable), '--prompt', prompt, '--output', str(staged)], timeout=120)
            regular_file(staged, 20_000_000)
            with staged.open('rb') as stream:
                header = stream.read(24)
            if header[:8] != b'\x89PNG\r\n\x1a\n' or len(header) < 24 or header[12:16] != b'IHDR':
                raise ExtensionError('Image engine did not produce a PNG artifact')
            width, height = int.from_bytes(header[16:20]), int.from_bytes(header[20:24])
            if not 0 < width <= 4096 or not 0 < height <= 4096:
                raise ExtensionError('Generated image dimensions exceed limits')
            shutil.copyfile(staged, destination)
        return {'artifact': str(destination), 'kind': 'image', 'width': width, 'height': height}
