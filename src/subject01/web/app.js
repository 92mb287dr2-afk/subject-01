"use strict";
const $ = id => document.getElementById(id);
const colors = {sensor:"#a8d5af",hidden:"#a9b1f1",motor:"#eda991",prediction:"#78ccdc"};
const roles = {sensor:"Ощущение",hidden:"Внутренний",motor:"Движение",prediction:"Предсказание"};
let state = null, token = "", cursor = -1, session = "", connected = false;
let addMode = false, selected = null, eventTotal = 0, newTotal = 0;
let pulses = [], eventsByType = {created:[],weight:[],signal:[],development:[],all:[]};
function currentBrain(){return state?.model_graphs?.[$("graphLayer").value] || state?.brain;}
let locations = {}, camera = {zoom:1,x:0,y:0}, drag = null;
let lastNetwork = 0, reducedMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
let worldPaused=false;
let noticeHeldUntil=0;
function notice(text, warn=false, hold=0){$("notice").textContent=text;$("notice").classList.toggle("warn",warn);if(hold>0)noticeHeldUntil=performance.now()+hold;}
function fmt(n){return Number(n).toFixed(3);}
function fit(canvas){
  const r=canvas.getBoundingClientRect(),d=Math.min(devicePixelRatio||1,2);
  const w=Math.round(r.width*d),h=Math.round(r.height*d);
  if(canvas.width!==w||canvas.height!==h){canvas.width=w;canvas.height=h;}
  const ctx=canvas.getContext("2d");ctx.setTransform(d,0,0,d,0,0);
  return {ctx,w:r.width,h:r.height};
}
function layout(nodes){
  const groups={sensor:[],hidden:[],motor:[],prediction:[]};
  nodes.forEach(n=>groups[n.group].push(n));
  const centers={sensor:[-.64,-.12],hidden:[0,-.10],motor:[.6,-.2],prediction:[.16,.54]};
  Object.entries(groups).forEach(([group,list])=>{
    list.forEach((n,i)=>{
      if(locations[n.id])return;
      const a=i*2.39996323, r=.08+Math.sqrt(i/list.length)*.23, c=centers[group];
      locations[n.id]={x:c[0]+Math.cos(a)*r,y:c[1]+Math.sin(a)*r};
    });
  });
}
function point(id,w,h){const p=locations[id];const k=Math.min(w,h)*.46*camera.zoom;
  return {x:w/2+p.x*k+camera.x,y:h/2+p.y*k+camera.y};}
function drawWorld(){
  const {ctx:c,w,h}=fit($("world"));c.clearRect(0,0,w,h);
  const g=c.createRadialGradient(w*.5,h*.45,5,w*.5,h*.45,w*.8);
  g.addColorStop(0,"#3c533a");g.addColorStop(1,"#213629");c.fillStyle=g;c.fillRect(0,0,w,h);
  // Fixed scenery is decoration, never a neural activity source.
  for(let i=0;i<170;i++){
    const x=((i*73+19)%997)/997*w,y=((i*151+43)%991)/991*h;
    c.strokeStyle=i%3?"#69805725":"#9eac7130";c.beginPath();c.moveTo(x,y);c.lineTo(x+2,y-4);c.stroke();
  }
  if(!state)return;
  const sx=w/state.width,sy=h/state.height;
  c.strokeStyle="#c3d9a825";c.lineWidth=1;c.strokeRect(sx,sy,w-2*sx,h-2*sy);
  for(const o of state.objects){
    const x=o.x*sx,y=o.y*sy,r=Math.max(6,o.radius*Math.min(sx,sy));
    c.fillStyle="#101f2160";c.beginPath();c.ellipse(x+3,y+4,r*1.3,r*.8,0,0,Math.PI*2);c.fill();
    c.fillStyle="#81917d";c.strokeStyle="#b0bba2";c.beginPath();c.moveTo(x-r,y-r*.3);
    c.lineTo(x-r*.5,y-r);c.lineTo(x+r*.6,y-r*.75);c.lineTo(x+r,y+r*.4);
    c.lineTo(x,y+r);c.lineTo(x-r,y+r*.5);c.closePath();c.fill();c.stroke();
  }
  const b=state.body,x=b.x*sx,y=b.y*sy,scale=Math.max(.8,Math.min(1.3,w/650));
  c.save();c.translate(x,y);
  c.fillStyle="#080f0a55";c.beginPath();c.ellipse(0,10,14*scale,6*scale,0,0,Math.PI*2);c.fill();
  c.strokeStyle="#c7e7a93d";c.setLineDash([3,5]);c.beginPath();c.arc(0,0,23*scale,0,Math.PI*2);c.stroke();c.setLineDash([]);
  c.scale(scale,scale);
  // Four actual joint angles for the candidate; legacy uses a fixed glyph.
  c.strokeStyle="#d7e9bc";c.lineWidth=3;c.lineCap="round";
  c.beginPath();c.moveTo(0,-4);c.lineTo(0,7);
  const joints=b.joints || [0,0,0,0];
  joints.forEach((angle,i)=>{const side=i%2 ? 1 : -1,base=i<2?-1:7;
    const a=side*(i<2?.95:.45)+angle*.5;
    c.moveTo(0,base);c.lineTo(Math.sin(a)*11,base+Math.cos(a)*11);});c.stroke();
  c.fillStyle="#edf3d6";c.beginPath();c.arc(0,-10,4.5,0,Math.PI*2);c.fill();
  c.strokeStyle="#a8d58b";c.lineWidth=1;c.beginPath();c.moveTo(0,0);c.lineTo(b.vx*6,b.vy*6);c.stroke();c.restore();
}
function drawBrain(now){
  const {ctx:c,w,h}=fit($("brain"));c.clearRect(0,0,w,h);
  for(let x=18;x<w;x+=24)for(let y=18;y<h;y+=24){c.fillStyle="#6c937b16";c.fillRect(x,y,1,1);}
  if(!state)return;const graph=currentBrain();layout(graph.nodes);
  const edges=new Map(graph.edges.map(e=>[e.id,e]));
  const threshold=Number($("threshold").value);
  for(const e of graph.edges){
    const a=point(e.source,w,h),b=point(e.target,w,h),fresh=e.created_tick>0 && state.tick-e.created_tick<40;
    const neighbor=!selected||e.source===selected||e.target===selected;
    c.globalAlpha=neighbor ? .65 : .10;c.strokeStyle=fresh?"#efd49a":e.weight<0?"#8b799e":"#678c78";
    c.lineWidth=.4+Math.abs(e.weight)*2;c.beginPath();c.moveTo(a.x,a.y);c.lineTo(b.x,b.y);c.stroke();
  }
  c.globalAlpha=1;
  pulses=pulses.filter(p=>now-p.start<520);
  if(!reducedMotion)for(const p of pulses){
    if(Math.abs(p.value)<threshold)continue;
    const edge=edges.get(p.edge);if(!edge)continue;
    if(selected&&edge.source!==selected&&edge.target!==selected)continue;
    const a=point(edge.source,w,h),b=point(edge.target,w,h),t=Math.min(1,(now-p.start)/520);
    c.globalAlpha=1-t*.8;c.fillStyle=p.value<0?"#c8b4ef":"#dcf2bb";
    c.beginPath();c.arc(a.x+(b.x-a.x)*t,a.y+(b.y-a.y)*t,1.2+Math.min(2,Math.abs(p.value)*2),0,Math.PI*2);c.fill();
  }
  c.globalAlpha=1;
  for(const n of graph.nodes){
    const p=point(n.id,w,h),a=Math.abs(n.value),r=3.5+a*3;
    c.fillStyle=colors[n.group];c.globalAlpha=.08+a*.12;c.beginPath();c.arc(p.x,p.y,r+7+a*7,0,Math.PI*2);c.fill();
    c.globalAlpha=.5+a*.5;c.beginPath();c.arc(p.x,p.y,r,0,Math.PI*2);c.fill();c.globalAlpha=1;
    if(selected===n.id){c.strokeStyle="#f4e6be";c.lineWidth=1;c.beginPath();c.arc(p.x,p.y,r+4,0,Math.PI*2);c.stroke();}
    c.fillStyle="#b6c7b7";c.font="9px system-ui";c.fillText(n.id,p.x+9,p.y+3);
  }
}
function inspect(){
  const n=currentBrain()?.nodes.find(n=>n.id===selected);
  if(!n){$("nodeInfo").textContent="Выбери нейрон на графе";return;}
  const incoming=currentBrain().edges.filter(e=>e.target===n.id).length;
  const outgoing=currentBrain().edges.filter(e=>e.source===n.id).length;
  $("nodeInfo").textContent=roles[n.group]+" "+n.id+"\nАктивность: "+fmt(n.value)+"\nВходов: "+incoming+" · выходов: "+outgoing;
}
function renderEvents(){
  const filter=$("filter").value, rows=eventsByType[filter].slice().reverse();
  $("events").replaceChildren();
  if(!rows.length){const p=document.createElement("p");p.className="empty";p.textContent=filter==="created"?"Новая связь появляется, когда правило обучения находит недостающую связь с ошибкой предсказания.":"Пока нет событий этого типа.";$("events").append(p);return;}
  const frag=document.createDocumentFragment();
  rows.forEach(e=>{
    const row=document.createElement("div");row.className="event "+e.kind;
    const text=e.kind==="created"?"Новая связь":e.kind==="weight"?"Вес изменён":e.kind==="signal"?"Сигнал":e.kind;
    const detail=e.edge ? e.edge+"  "+(e.kind==="weight"?fmt(e.before)+" → "+fmt(e.weight):fmt(e.value??e.weight)) :
      e.kind==="action_models" ? "Матрицы и вычисленные прогнозы сохранены в полном журнале" : JSON.stringify(e).slice(0,450);
    ["t "+e.tick,text,detail].forEach(t=>{const s=document.createElement("span");s.textContent=t;row.append(s);});frag.append(row);
  });$("events").append(frag);
}
function accept(data){
  const recovering = !connected && !!state;
  if(session && session!==data.session){cursor=-1;pulses=[];eventsByType={created:[],weight:[],signal:[],development:[],all:[]};eventTotal=0;newTotal=0;notice("Сервер перезапущен. Восстановлено сохранённое состояние.",true);}
  session=data.session;cursor=data.cursor;state=data.snapshot;lastNetwork=performance.now();connected=data.running&&!data.error;
  worldPaused=!!data.paused;$("pauseWorld").hidden=!state.development;$("pauseWorld").textContent=worldPaused?"Продолжить":"Пауза";
  const now=performance.now();
  for(const batch of data.batches)for(const e of batch.events){
    eventTotal++;if(e.kind==="created")newTotal++;
    if(e.kind==="signal"&&$("graphLayer").value==="brain")pulses.push({edge:e.edge,value:e.value,start:now});
    const category=eventsByType[e.kind] ? e.kind : "development";
    eventsByType[category].push(e);eventsByType.all.push(e);
  }
  if($("graphLayer").value!=="brain" && data.batches.length){
    for(const e of currentBrain().edges)if(e.signal)pulses.push({edge:e.id,value:e.signal,start:now});
  }
  for(const key of Object.keys(eventsByType))eventsByType[key]=eventsByType[key].slice(-200);
  if(data.error)notice("Симуляция остановилась: "+data.error,true);
  else if(data.gap)notice("Пропущен участок онлайн-потока. Все записанные сигналы доступны в полном журнале.",true);
  else if(!data.running)notice("Мир остановлен. Отображается последнее состояние.",true);
  else if(worldPaused&&performance.now()>noticeHeldUntil)notice("Техническая пауза: время мира не идёт.");
  else if(recovering)notice("Связь восстановлена. Снова показано актуальное состояние мира.");
  else if(!$("notice").classList.contains("warn")&&performance.now()>noticeHeldUntil)notice("Прямые данные сети · все ненулевые передачи журналируются · это не биологические спайки");
  const b=state.body;$("energy").textContent=Math.round(b.energy*100)+"%";$("energyBar").value=b.energy;
  $("speed").textContent=Math.hypot(b.vx,b.vy).toFixed(2);$("objects").textContent=state.objects.length;
  $("tick").textContent=state.tick;$("edges").textContent=currentBrain().edges.length;$("nodeCount").textContent=currentBrain().nodes.length+" узла";
  $("created").textContent=newTotal;$("error").textContent=currentBrain().error===null?"—":currentBrain().error.toFixed(4);
  $("developmentPanel").hidden=!state.development;
  $("graphLayer").disabled=!state.model_graphs;
  if(state.development){const d=state.development;
    $("modeLabel").textContent="кандидат · допуск к рождению закрыт";
    $("bodyMode").textContent=d.mode==="RECOVERING"?"Восстановление тела":"Тело активно";
    $("origin").textContent=state.continuity.origin_id.slice(0,8);
    $("health").textContent=Math.round(d.health*100)+"%";$("material").textContent=fmt(d.material);
    $("strength").textContent=fmt(d.strength);$("memories").textContent=d.memories;
    $("hypothesis").textContent=d.hypothesis ? "Гипотеза №"+d.hypothesis.id+" · "+d.hypothesis.phase+" · прогноз и реальная проба разделены" : d.repair ? "Компенсация изменения: время и ресурсы расходуются" : "Накопление опыта для следующей гипотезы";
    $("modelErrors").textContent="Ошибка управления: "+fmt(d.controller_error)+" · исследователя: "+fmt(d.researcher_error)+" · методы по каналам: "+d.channel_methods.join(", ");
  }
  $("eventCount").textContent=eventTotal.toLocaleString("ru")+" полученных событий";
  $("clock").textContent=Math.floor(state.time/60).toString().padStart(2,"0")+":"+Math.floor(state.time%60).toString().padStart(2,"0");
  inspect();renderEvents();
}
async function poll(){
  try{
    if(!token){const r=await fetch("/api/session",{signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error("HTTP "+r.status);token=(await r.json()).token;}
    const r=await fetch("/api/frames?after="+cursor,{signal:AbortSignal.timeout(5000)});if(!r.ok)throw Error("HTTP "+r.status);
    const data=await r.json();
    if(session&&session!==data.session){cursor=-1;const fresh=await fetch("/api/frames?after=-1");accept(await fresh.json());}else accept(data);
  }catch(e){connected=false;token="";notice("Нет связи с процессом симуляции. Показано последнее полученное состояние.",true);}
  setTimeout(poll,150);
}
async function post(path,payload){
  const r=await fetch(path,{method:"POST",headers:{"Content-Type":"application/json","X-Observer-Token":token},body:JSON.stringify(payload),signal:AbortSignal.timeout(5000)});
  const result=await r.json();if(!r.ok)throw Error(result.error||r.status);return result;
}
$("add").onclick=()=>{addMode=!addMode;$("add").classList.toggle("active",addMode);$("worldHelp").textContent=addMode?"Нажми на поляну, чтобы добавить объект":"Наблюдение в реальном времени";};
$("world").onclick=async e=>{
  if(!addMode||!state)return;
  const r=$("world").getBoundingClientRect(),x=(e.clientX-r.left)/r.width*state.width,y=(e.clientY-r.top)/r.height*state.height;
  try{const result=await post("/api/command",{request_id:crypto.randomUUID(),kind:"spawn_object",payload:{x,y,kind:"stone"}});notice("Объект поставлен в очередь · вмешательство №"+result.event_id,false,4000);}
  catch(e){notice("Не удалось добавить объект: "+e.message,true);}
};
$("save").onclick=async()=>{const b=$("save");b.disabled=true;try{await post("/api/save",{});notice("Состояние мира, тела и сети сохранено.",false,4000);}catch(e){notice("Ошибка сохранения: "+e.message,true);}finally{b.disabled=false;}};
$("filter").onchange=renderEvents;
$("pauseWorld").onclick=async()=>{try{await post(worldPaused?"/api/resume":"/api/pause",{});}catch(e){notice(e.message,true);}};
$("graphLayer").onchange=()=>{locations={};pulses=[];selected=null;inspect();$("graphNote").textContent=$("graphLayer").value==="brain"?"Передачи сенсорной сети":$("graphLayer").value==="researcher"?"Составной прогноз исследователя · отдельный темп обучения каждого канала":"Последний вычисленный прогноз · импульсы = вход × вес";};
$("inspectState").onclick=async()=>{try{const r=await fetch("/api/state");if(!r.ok)throw Error(r.status);$("fullState").textContent=JSON.stringify(await r.json(),null,2);}catch(e){notice(e.message,true);}};
let overrideGrant=null;
$("previewOverride").onclick=async()=>{try{const operation=$("overrideOperation").value;
  const target=operation.includes("kernel")||operation==="edit_recovery_policy"?"kernel":operation==="edit_model_weights"?$("modelTarget").value:Number($("memoryTarget").value);
  const replacement=operation.startsWith("edit_")||operation==="replace_memory"?JSON.parse($("overrideReplacement").value):null;
  overrideGrant=await post("/api/override/preview",{operation,target,replacement});$("overridePreview").hidden=false;
  $("overrideConfirmation").value="";$("overrideConsequence").textContent=overrideGrant.consequence+" Новое содержание: "+JSON.stringify(overrideGrant.replacement)+". В течение 60 секунд введите: "+overrideGrant.required_confirmation;
}catch(e){notice(e.message,true);}};
$("confirmOverride").onclick=async()=>{if(!overrideGrant)return;try{
  await post("/api/override/confirm",{token:overrideGrant.token,confirmation:$("overrideConfirmation").value});
  notice("Исключительное вмешательство записано в аудит.",false,4000);
}catch(e){notice(e.message,true);}finally{overrideGrant=null;$("overridePreview").hidden=true;}};
$("threshold").oninput=()=>$("thresholdValue").textContent=Number($("threshold").value).toFixed(2);
$("expand").onclick=()=>{const on=$("brainPanel").classList.toggle("expanded");$("expand").textContent=on?"↙ Свернуть":"↗ Развернуть";};
function zoom(factor){camera.zoom=Math.min(4,Math.max(.4,camera.zoom*factor));}
$("zoomIn").onclick=()=>zoom(1.2);$("zoomOut").onclick=()=>zoom(1/1.2);$("reset").onclick=()=>{camera={zoom:1,x:0,y:0};locations={};selected=null;inspect();};
$("brain").addEventListener("wheel",e=>{e.preventDefault();zoom(e.deltaY<0?1.1:1/1.1);},{passive:false});
$("brain").onpointerdown=e=>{
  const rect=$("brain").getBoundingClientRect(),x=e.clientX-rect.left,y=e.clientY-rect.top;
  let hit=null,best=20;
  for(const id of Object.keys(locations)){const p=point(id,rect.width,rect.height),d=Math.hypot(p.x-x,p.y-y);if(d<best){hit=id;best=d;}}
  selected=hit;inspect();drag={id:hit,x:e.clientX,y:e.clientY};$("brain").setPointerCapture(e.pointerId);
};
$("brain").onpointermove=e=>{
  if(!drag)return;const dx=e.clientX-drag.x,dy=e.clientY-drag.y;
  if(drag.id){const r=$("brain").getBoundingClientRect(),k=Math.min(r.width,r.height)*.46*camera.zoom;locations[drag.id].x+=dx/k;locations[drag.id].y+=dy/k;}
  else{camera.x+=dx;camera.y+=dy;}drag.x=e.clientX;drag.y=e.clientY;
};
$("brain").onpointerup=$("brain").onpointercancel=()=>drag=null;
document.addEventListener("keydown",e=>{if(e.key==="Escape"){$("brainPanel").classList.remove("expanded");$("expand").textContent="↗ Развернуть";}});
let lastDraw=0;
function draw(now){
  if(now-lastDraw>=32){lastDraw=now;drawWorld();drawBrain(now);
    const live=connected&&now-lastNetwork<3000;$("lamp").classList.toggle("online",live&&!worldPaused);$("connection").textContent=live?(worldPaused?"МИР НА ПАУЗЕ":"МИР АКТИВЕН"):"НЕТ LIVE-СВЯЗИ";
  }requestAnimationFrame(draw);
}
poll();requestAnimationFrame(draw);
