const {app,BrowserWindow,ipcMain,dialog,safeStorage,protocol,net}=require('electron');
const fs=require('node:fs/promises');
const path=require('node:path');
const {pathToFileURL}=require('node:url');
const {spawn}=require('node:child_process');
const crypto=require('node:crypto');
const {imageExtensions,imagePlan,exportImages}=require('./flow-images');
protocol.registerSchemesAsPrivileged([{scheme:'preview',privileges:{standard:true,secure:true,stream:true,supportFetchAPI:true}}]);
let win,groups=[],busy=false,key='',status='Adicione as pastas com três cenas.',session;
let imageProducts=[];
const extensions=new Set(['.mp4','.mov','.m4v','.webm']);
const keyFile=()=>path.join(app.getPath('userData'),'assembly.key');
const snapshot=()=>({groups:groups.map(g=>({...g,clips:g.clips.map(p=>path.basename(p)),url:g.output?`preview://video/${g.id}`:null})),imageProducts:imageProducts.map((p,index)=>({id:p.id,name:`Produto ${String(index+1).padStart(2,'0')}`,images:p.images.map(file=>path.basename(file)),filenames:imagePlan(imageProducts).filter(item=>item.productIndex===index).map(item=>item.name)})),busy,status,hasKey:!!key});
function update(){if(win&&!win.isDestroyed())win.webContents.send('state',snapshot());}
function requireIdle(){if(busy)throw Error('Aguarde a edição em andamento.');}
async function folders(root,depth=0){
  if(depth>8)return [];
  const entries=await fs.readdir(root,{withFileTypes:true});
  const clips=entries.filter(e=>e.isFile()&&extensions.has(path.extname(e.name).toLowerCase())).map(e=>path.join(root,e.name)).sort((a,b)=>path.basename(a).localeCompare(path.basename(b),'pt',{numeric:true}));
  if(clips.length){if(clips.length!==3)throw Error(`${root}: precisa conter exatamente três vídeos (encontrados ${clips.length}).`);return [{root,clips}];}
  const result=[];
  for(const entry of entries.filter(e=>e.isDirectory()))result.push(...await folders(path.join(root,entry.name),depth+1));
  return result;
}
function processVideo(g,assembly){
  return new Promise((resolve,reject)=>{
    const output=path.join(session,`${g.id}.mp4`);
    const args=[path.join(__dirname,'worker','edit_video.py'),output,...g.clips,...g.speeds.map(String)];
    if(assembly)args.push('--assemblyai');
    const child=spawn(path.join(__dirname,'python','python.exe'),args,{windowsHide:true,stdio:['pipe','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
    let errors='',buffer='';
    child.stdin.on('error',()=>{});
    child.stdin.end(assembly?key+'\n':'');
    child.stdout.on('data',chunk=>{
      buffer+=chunk.toString();
      const lines=buffer.split(/\r?\n/);buffer=lines.pop();
      for(const line of lines)if(line&&!line.startsWith('{')){status=`${g.name}: ${line}`;update();}
    });
    child.stderr.on('data',chunk=>errors=(errors+chunk.toString()).slice(-6000));
    child.on('error',reject);
    child.on('close',async code=>{
      if(code!==0)return reject(Error(errors||`Processo encerrou com código ${code}.`));
      try{
        const report=JSON.parse(await fs.readFile(output.replace(/\.mp4$/,'.editing.json'),'utf8'));
        g.output=output;
        g.message=`Pronto · análise ${report.timings.analysisSeconds}s · edição ${report.timings.renderSeconds}s`;
        resolve();
      }catch(e){reject(e);}
    });
  });
}
ipcMain.handle('editor',async(event,name,data)=>{
  if(event.sender!==win.webContents)throw Error('Origem inválida.');
  try{
    if(name==='state')return snapshot();
    if(name==='addImages'){
      requireIdle();busy=true;update();
      try{
        const images=[];
        for(const title of ['Imagem para a Cena 1','Imagem para as Cenas 2 e 3']){
          const result=await dialog.showOpenDialog(win,{title,properties:['openFile'],filters:[{name:'Imagens',extensions:imageExtensions}]});
          if(result.canceled)return {...snapshot(),busy:false};
          images.push(result.filePaths[0]);
        }
        imagePlan([{images}]);
        imageProducts.push({id:crypto.randomUUID(),images});
        status='Duas imagens adicionadas. Exporte para enviar ao Flow na ordem numérica.';
      }finally{busy=false;update();}
    }else if(name==='removeImages'){
      requireIdle();imageProducts=imageProducts.filter(p=>p.id!==data);status='Produto removido das imagens. Arquivos originais preservados.';
    }else if(name==='exportImages'){
      requireIdle();imagePlan(imageProducts);busy=true;update();
      try{
        const result=await dialog.showOpenDialog(win,{title:'Onde salvar as imagens para o Flow?',properties:['openDirectory','createDirectory']});
        if(result.canceled)return {...snapshot(),busy:false};
        const exported=await exportImages(imageProducts,result.filePaths[0]);
        status=`${exported.count} imagens exportadas para ${exported.directory}. No Flow, siga a ordem numérica dos arquivos.`;
      }finally{busy=false;update();}
    }else if(name==='add'){
      requireIdle();
      const result=await dialog.showOpenDialog(win,{properties:['openDirectory','multiSelections']});
      if(result.canceled)return snapshot();
      const found=[];
      for(const folder of result.filePaths)found.push(...await folders(folder));
      for(const item of found)if(!groups.some(g=>g.root===item.root))groups.push({...item,id:crypto.randomUUID(),name:path.basename(item.root),speeds:[1.3,1.15,1.15],message:'Aguardando',output:null});
      status=`${groups.length} produtos na fila.`;
    }else if(name==='key'){
      requireIdle();
      if(!safeStorage.isEncryptionAvailable())throw Error('Proteção de credenciais do Windows indisponível.');
      const value=String(data||'').trim();
      if(!value)throw Error('Informe a chave.');
      await fs.writeFile(keyFile(),safeStorage.encryptString(value));
      key=value;status='Chave salva com proteção do Windows.';
    }else if(name==='forgetKey'){
      requireIdle();await fs.rm(keyFile(),{force:true});key='';status='Chave removida.';
    }else if(name==='remove'){
      requireIdle();groups=groups.filter(g=>g.id!==data);status='Produto removido da fila. Os arquivos foram preservados.';
    }else if(name==='speeds'){
      requireIdle();const g=groups.find(g=>g.id===data.id);
      if(!g||!Array.isArray(data.values)||data.values.length!==3||data.values.some(v=>typeof v!=='number'||!Number.isFinite(v)||v<.5||v>2))throw Error('Velocidades: use valores de 0,50 a 2,00.');
      g.speeds=data.values;g.output=null;g.message='Velocidades alteradas: gere nova prévia.';
    }else if(name==='generate'){
      requireIdle();
      const selected=data.id?groups.filter(g=>g.id===data.id):[...groups];
      if(!selected.length)throw Error('Adicione um produto.');
      const assembly=!!data.assembly;
      if(assembly&&!key)throw Error('Salve a chave do AssemblyAI nas configurações.');
      busy=true;update();
      try{
      if(assembly){
        const answer=await dialog.showMessageBox(win,{type:'question',buttons:['Cancelar','Gerar'],defaultId:0,cancelId:0,message:`Enviar o áudio de até ${selected.length*3} cenas ao AssemblyAI?`,detail:'A transcrição pode gerar cobrança na sua conta. Os vídeos não são enviados.'});
        if(answer.response!==1){busy=false;return snapshot();}
      }
        for(const g of selected){g.output=null;g.message='Editando…';update();try{await processVideo(g,assembly);}catch(e){g.message='Falha: '+e.message;throw e;}}
        status='Edição concluída. Pronto para exportar.';
      }finally{busy=false;update();}
    }else if(name==='export'){
      requireIdle();
      const selected=groups.filter(g=>g.output&&(!data||g.id===data));
      if(!selected.length)throw Error('Gere uma prévia antes de exportar.');
      const result=await dialog.showOpenDialog(win,{properties:['openDirectory','createDirectory']});
      if(result.canceled)return snapshot();
      for(const g of selected){
        const base=g.name.replace(/[<>:"/\\|?*\x00-\x1F]/g,'_').replace(/[. ]+$/g,'')||'video';
        for(let n=0;;n++){
          const file=path.join(result.filePaths[0],`${base}${n?'_'+n:''}.mp4`);
          try{await fs.copyFile(g.output,file,require('node:fs').constants.COPYFILE_EXCL);break;}catch(e){if(e.code!=='EEXIST')throw e;}
        }
      }
      status=`${selected.length} vídeos exportados para ${result.filePaths[0]}.`;
    }else throw Error('Comando inválido.');
    update();return snapshot();
  }catch(e){status=e.message;update();return {...snapshot(),error:e.message};}
});
if(!app.requestSingleInstanceLock())app.quit();
else{
  app.on('second-instance',()=>{if(win){if(win.isMinimized())win.restore();win.show();win.focus();}});
  app.whenReady().then(async()=>{
    session=path.join(app.getPath('userData'),'previews',crypto.randomUUID());await fs.mkdir(session,{recursive:true});
    try{key=safeStorage.decryptString(await fs.readFile(keyFile()));}catch{}
    protocol.handle('preview',request=>{
      const id=new URL(request.url).pathname.slice(1);
      const g=groups.find(g=>g.id===id&&g.output);
      if(!g)return new Response('Not found',{status:404});
      return net.fetch(pathToFileURL(g.output).href,{headers:request.headers});
    });
    win=new BrowserWindow({width:1200,height:850,minWidth:980,minHeight:720,title:'EDITOR DO NEIS',backgroundColor:'#111214',webPreferences:{preload:path.join(__dirname,'preload.js'),contextIsolation:true,nodeIntegration:false,sandbox:true}});
    win.webContents.setWindowOpenHandler(()=>({action:'deny'}));
    win.webContents.on('will-navigate',event=>event.preventDefault());
    win.on('close',event=>{if(busy){event.preventDefault();dialog.showMessageBox(win,{message:'Aguarde a edição terminar antes de fechar.'});}});
    await win.loadFile(path.join(__dirname,'index.html'));
  }).catch(e=>{dialog.showErrorBox('Falha ao iniciar',e.message);app.quit();});
  app.on('window-all-closed',()=>app.quit());
}
