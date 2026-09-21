'use strict';
const report=JSON.parse(document.getElementById('report-data').textContent);
const $=id=>document.getElementById(id), audio=$('audio'), NS='http://www.w3.org/2000/svg';
let reference={...report.reference},events=report.events.map(e=>({...e})),selected=null,projected=[];
let start=0,span=Math.min(12,report.duration),frame=null,context=null,timer=null,scheduled=new Map();
const fmt=t=>{const n=Math.max(0,Math.round(t*10));return `${Math.floor(n/600)}:${((n%600)/10).toFixed(1).padStart(4,'0')}`;};
const ms=v=>v===null?'—':`${v>0?'+':''}${v.toFixed(1)} ms`;
const status=text=>$('status').textContent=text;
function element(tag,attrs={},text){const n=document.createElementNS(NS,tag);for(const [k,v]of Object.entries(attrs))n.setAttribute(k,v);if(text!==undefined)n.textContent=text;return n;}
function settings(){for(const k of ['bpm','offset','beats','subdivisions'])$(k).value=reference[k];$('span').value=span;$('loop-end').value=Math.min(12,report.duration);$('offset').max=report.duration;}
function visible(){return projected.filter(e=>e.time>=start&&e.time<=start+span);}
const x=t=>60+(t-start)/span*960;
function seek(t){if(audio.readyState>=1&&!audio.error){audio.currentTime=Math.max(0,Math.min(t,report.duration));cancelClicks();updateCursor();}}
function select(id){selected=id;const e=events.find(e=>e.id===id);$('event-time').disabled=!e;$('move').disabled=!e;$('toggle').disabled=!e;if(e){$('event-time').value=e.time;$('toggle').textContent=e.enabled?'Exclude attack':'Include attack';$('selection').textContent=`Attack at ${fmt(e.time)}`;}else $('selection').textContent='No attack selected';draw();}
function base(svg,height){svg.replaceChildren();svg.append(element('rect',{x:60,y:20,width:960,height:height-50,fill:'#f8fafc'}));for(let i=0;i<=6;i++){const t=start+span*i/6;svg.append(element('text',{x:x(t),y:height-7,'text-anchor':'middle'},fmt(t)));}}
function dotEvents(node,e){node.setAttribute('data-id',e.id);node.setAttribute('tabindex','0');node.setAttribute('role','button');node.setAttribute('aria-label',`Edit attack at ${fmt(e.time)}`);node.append(element('title',{},`Attack ${e.time.toFixed(3)} s${e.error===undefined?'':` · ${ms(e.error)}`}`));node.addEventListener('click',event=>{event.stopPropagation();select(e.id)});node.addEventListener('keydown',event=>{if(event.key==='Enter'){event.preventDefault();select(e.id)}});}
function draw(){
  const a=$('attacks'),t=$('timing');base(a,210);base(t,220);
  const step=60/reference.bpm/reference.subdivisions,slots=reference.beats*reference.subdivisions;
  for(let i=Math.ceil((start-reference.offset)/step);reference.offset+i*step<=start+span;i++){
    const time=reference.offset+i*step,bar=i%slots===0,beat=i%reference.subdivisions===0;
    a.append(element('line',{x1:x(time),x2:x(time),y1:20,y2:160,stroke:bar?'#475569':beat?'#bbc8d8':'#e2e8f0','stroke-width':bar?2:1}));
    if(beat && span*reference.bpm/60<75)a.append(element('text',{x:x(time)+3,y:32},bar?`Bar ${Math.floor(i/slots)+1}`:`${((i/reference.subdivisions)%reference.beats+reference.beats)%reference.beats+1}`));
  }
  const peak=Math.max(...report.waveform,1e-8),points=[];
  for(let i=Math.max(0,Math.floor(start/report.waveform_step));i<report.waveform.length&&i*report.waveform_step<=start+span;i++){
    const time=i*report.waveform_step,v=report.waveform[i]/peak*47;points.push(`${x(time)},${102-v}`);points.push(`${x(time)},${102+v}`);
  }
  a.append(element('polyline',{points:points.join(' '),fill:'none',stroke:'#cbd5e1','stroke-width':.8}));
  for(const e of events.filter(e=>e.time>=start&&e.time<=start+span)){
    const node=element('line',{x1:x(e.time),x2:x(e.time),y1:55,y2:155,stroke:e.id===selected?'#c1780a':e.enabled?'#285bea':'#a1a1aa','stroke-width':e.id===selected?4:2,'stroke-dasharray':e.enabled?'none':'3 4'});dotEvents(node,e);a.append(node);
  }
  const limit=step*500;
  for(const value of [-limit,0,limit]){const y=105-value/limit*70;t.append(element('line',{x1:60,x2:1020,y1:y,y2:y,stroke:value===0?'#64748b':'#dbe3ec'}));t.append(element('text',{x:54,y:y+4,'text-anchor':'end'},`${value>0?'+':''}${Math.round(value)}`));}
  t.append(element('text',{x:8,y:20},'ms'));
  for(const e of visible()){const node=element('circle',{cx:x(e.time),cy:105-e.error/limit*70,r:e.id===selected?6:4,fill:e.id===selected?'#c1780a':'#285bea'});dotEvents(node,e);t.append(node);}
  for(const svg of [a,t])svg.append(element('line',{class:'cursor',x1:0,x2:0,y1:20,y2:170}));updateCursor();
}
function row(body,values){const tr=document.createElement('tr');for(const v of values){const td=document.createElement('td');if(v instanceof Node)td.append(v);else td.textContent=v;tr.append(td)}body.append(tr);}
function summary(){const list=visible(),s=Rhythm.stats(list);$('count').textContent=s.count;$('mean').textContent=ms(s.mean);$('spread').textContent=s.spread===null?'—':`${s.spread.toFixed(1)} ms`;
  $('slots').replaceChildren();for(let slot=0;slot<reference.beats*reference.subdivisions;slot++){
    const group=list.filter(e=>e.slot===slot),s=Rhythm.stats(group),beat=Math.floor(slot/reference.subdivisions)+1,sub=slot%reference.subdivisions;
    row($('slots'),[sub===0?`${beat}`:`${beat} + ${sub}/${reference.subdivisions}`,s.count,ms(s.mean),s.spread===null?'—':`${s.spread.toFixed(1)} ms`,group.length?`${Math.round(group.reduce((n,e)=>n+e.strength,0)/group.length*100)}%`:'—']);
  }
  const groups=new Map();for(const e of projected){if(e.bar<0)continue;if(!groups.has(e.bar))groups.set(e.bar,[]);groups.get(e.bar).push(e);}
  $('bars').replaceChildren();[...groups].map(([bar,items])=>({bar,...Rhythm.stats(items)})).filter(g=>g.count>=4).sort((a,b)=>b.spread-a.spread).slice(0,12).forEach(g=>{
    const time=reference.offset+g.bar*reference.beats*60/reference.bpm,button=document.createElement('button');button.textContent='Review bar';button.onclick=()=>{setView(time);$('loop-start').value=time.toFixed(3);$('loop-end').value=Math.min(report.duration,time+reference.beats*60/reference.bpm).toFixed(3);$('loop').checked=true;seek(time);};row($('bars'),[g.bar+1,fmt(time),g.count,`${g.spread.toFixed(1)} ms`,button]);
  });
}
function refresh(){projected=Rhythm.project(events,reference);draw();summary();}
function setView(value){start=Math.max(0,Math.min(value,Math.max(0,report.duration-span)));$('start').value=start.toFixed(3);refresh();}
function changeReference(){const next={};for(const k of ['bpm','offset','beats','subdivisions'])next[k]=Number($(k).value);if(!Rhythm.validate(next)||next.offset>report.duration){status('Use BPM 30–300 and a beat position inside the recording.');return;}reference=next;cancelClicks();status('Grid updated. Save a session to keep your changes.');refresh();}
for(const k of ['bpm','offset','beats','subdivisions'])$(k).addEventListener('change',changeReference);
for(const [id,factor]of [['half',.5],['double',2]])$(id).onclick=()=>{const next=reference.bpm*factor;if(next<30||next>300){status('Reference BPM must stay between 30 and 300.');return;}$('bpm').value=next;changeReference();};
$('anchor').onclick=()=>{$('offset').value=audio.currentTime.toFixed(3);changeReference();};
$('start').onchange=()=>{const n=Number($('start').value);if(Number.isFinite(n))setView(n);};
$('span').onchange=()=>{const n=Number($('span').value);if(Number.isFinite(n)&&n>=2&&n<=60){span=Math.min(n,report.duration);setView(start)}};
$('previous').onclick=()=>setView(start-span);$('next').onclick=()=>setView(start+span);$('here').onclick=()=>setView(audio.currentTime-span/4);
for(const svg of [$('attacks'),$('timing')])svg.addEventListener('click',e=>{const point=new DOMPoint(e.clientX,e.clientY).matrixTransform(svg.getScreenCTM().inverse());if(point.x<60||point.x>1020||point.y<20||point.y>170)return;const time=start+(point.x-60)/960*span;if(e.shiftKey)add(time);else seek(time);});
function add(time){const id=Math.max(-1,...events.map(e=>e.id))+1;events.push({id,time:Math.max(0,Math.min(time,report.duration)),strength:.5,enabled:true,manual:true});refresh();select(id);status('Manual attack added.');}
$('add').onclick=()=>add(audio.currentTime);
$('move').onclick=()=>{const time=Number($('event-time').value);if(!Number.isFinite(time)||time<0||time>report.duration){status('Attack time must be within the recording.');return;}const e=events.find(e=>e.id===selected);if(e){e.time=time;e.manual=true;refresh();select(e.id);}};
$('toggle').onclick=()=>{const e=events.find(e=>e.id===selected);if(e){e.enabled=!e.enabled;refresh();select(e.id);}};
function loopBounds(){const left=Number($('loop-start').value),right=Number($('loop-end').value);return Number.isFinite(left)&&Number.isFinite(right)&&left>=0&&right<=report.duration&&right-left>=.25?[left,right]:null;}
$('loop-view').onclick=()=>{$('loop-start').value=start.toFixed(3);$('loop-end').value=Math.min(report.duration,start+span).toFixed(3);};
for(const id of ['loop','loop-start','loop-end'])$(id).addEventListener('change',()=>{cancelClicks();if($('loop').checked&&!loopBounds()){$('loop').checked=false;status('Loop must be at least 0.25 seconds and within the recording.');}});
function updateCursor(){const t=audio.currentTime||0;$('clock').textContent=`${fmt(t)} / ${fmt(report.duration)}`;document.querySelectorAll('.cursor').forEach(n=>{n.setAttribute('x1',x(t));n.setAttribute('x2',x(t));n.style.display=t>=start&&t<=start+span?'':'none'});}
function tick(){const loop=loopBounds();if($('loop').checked&&loop&&!audio.paused&&(audio.currentTime>=loop[1]||audio.currentTime<loop[0]))seek(loop[0]);if($('follow').checked&&!audio.paused&&(audio.currentTime>start+span||audio.currentTime<start))setView(audio.currentTime);updateCursor();if(!audio.paused)frame=requestAnimationFrame(tick);}
audio.addEventListener('play',()=>{cancelAnimationFrame(frame);tick();});audio.addEventListener('pause',()=>{cancelAnimationFrame(frame);cancelClicks();updateCursor();});audio.addEventListener('timeupdate',updateCursor);audio.addEventListener('seeking',cancelClicks);audio.addEventListener('ended',()=>{const loop=loopBounds();if($('loop').checked&&loop){seek(loop[0]);audio.play().catch(e=>status(e.message));}});
audio.addEventListener('error',()=>status('Audio could not load. Keep playback.m4a beside the HTML report.'));
function cancelClicks(){for(const {osc}of scheduled.values()){try{osc.stop()}catch{}}scheduled.clear();}
function scheduleClick(){if(!context||context.state!=='running'||!$('click').checked||audio.paused||audio.seeking||audio.readyState<3)return;
  const beat=60/reference.bpm,t=audio.currentTime;for(const [i,node]of scheduled){if(node.time+.05<t)scheduled.delete(i);}
  for(let i=Math.ceil((t-reference.offset)/beat);reference.offset+i*beat<t+.12*audio.playbackRate;i++){
    const time=reference.offset+i*beat,loop=loopBounds();if(time<0||time>report.duration||scheduled.has(i)||($('loop').checked&&loop&&time>=loop[1]))continue;
    const when=context.currentTime+(time-t)/audio.playbackRate,osc=context.createOscillator(),gain=context.createGain();osc.frequency.value=i%reference.beats===0?1100:750;gain.gain.setValueAtTime(.10,when);gain.gain.exponentialRampToValueAtTime(.0001,when+.025);osc.connect(gain);gain.connect(context.destination);osc.start(when);osc.stop(when+.03);osc.onended=()=>{osc.disconnect();gain.disconnect()};scheduled.set(i,{osc,time});
  }
}
$('click').onchange=async()=>{cancelClicks();clearInterval(timer);if(!$('click').checked)return;try{context=context||new (window.AudioContext||window.webkitAudioContext)();await context.resume();timer=setInterval(scheduleClick,25);}catch(e){$('click').checked=false;status(`Reference click unavailable: ${e.message}`);}};
audio.addEventListener('ratechange',cancelClicks);
audio.addEventListener('waiting',cancelClicks);
function download(name,text,type){const url=URL.createObjectURL(new Blob([text],{type})),link=document.createElement('a');link.href=url;link.download=name;link.click();setTimeout(()=>URL.revokeObjectURL(url),10000);}
$('save').onclick=()=>download('rhythm-session.json',JSON.stringify({version:1,source:report.source,duration:report.duration,reference,events},null,2),'application/json');
$('csv').onclick=()=>{const columns=['time','target','error','bar','slot','strength'];download('rhythm-timing.csv','time_seconds,grid_seconds,offset_ms,bar_1_based,slot_1_based,relative_attack_strength\n'+[...projected].sort((a,b)=>a.time-b.time).map(e=>columns.map(k=>k==='bar'||k==='slot'?e[k]+1:e[k]).join(',')).join('\n'),'text/csv');};
$('import').onchange=async()=>{try{const file=$('import').files[0];if(!file)return;if(file.size>20e6)throw Error('Session file too large');const value=Rhythm.session(JSON.parse(await file.text()),report);reference=value.reference;events=value.events;selected=null;settings();cancelClicks();refresh();select(null);status('Session restored.');}catch(e){status(e.message)}finally{$('import').value='';}};
$('reset').onclick=()=>{if(!confirm('Reset the grid and attack edits? Save a session first if you want to keep them.'))return;reference={...report.reference};events=report.events.map(e=>({...e}));settings();cancelClicks();refresh();select(null);status('Original detections restored.');};
$('reference-note').textContent=report.reference_supplied?'Initial BPM supplied by you. Phase and meter still need checking.':'BPM and subdivision alignment are initial estimates; beat 1 and meter have not been identified. Use ½ BPM if the pulse sounds half as fast.';
settings();refresh();
