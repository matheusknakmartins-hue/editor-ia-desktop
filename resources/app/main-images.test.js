const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const vm=require('node:vm');

test('batch import requires review, allows swaps, and exports every image once',async t=>{
  const folder=await fs.mkdtemp(path.join(os.tmpdir(),'main-images-'));
  t.after(()=>fs.rm(folder,{recursive:true,force:true}));
  const files=[];
  for(let i=0;i<20;i++){
    const file=path.join(folder,`input-${i}.png`);await fs.writeFile(file,Buffer.from([i]));files.push(file);
  }
  let invoke,window;
  const dialogs=[];
  const thumb={isEmpty:()=>false,getSize:()=>({width:1,height:1}),resize(){return this;},toDataURL:()=>''};
  let ready;
  const initialized=new Promise(resolve=>{ready=resolve;});
  class BrowserWindow{
    constructor(){window=this;this.webContents={send(){},setWindowOpenHandler(){},on(){}};}
    on(){} async loadFile(){ready();} isDestroyed(){return false;}
  }
  const electron={BrowserWindow,nativeImage:{createFromPath:()=>thumb},
    ipcMain:{handle:(name,callback)=>{invoke=callback;}},
    dialog:{showOpenDialog:async()=>{assert.ok(dialogs.length);return dialogs.shift();}},
    protocol:{registerSchemesAsPrivileged(){},handle(){}},net:{},safeStorage:{decryptString(){}},
    app:{requestSingleInstanceLock:()=>true,on(){},whenReady:()=>Promise.resolve(),getPath:()=>folder}};
  let fail=false;
  const localImages={analyzeImages:async(selected,options)=>{
    if(fail)throw Error('model download unavailable');
    if(options.sceneSplit!==undefined){
      assert.equal(options.sceneSplit,1);
      assert.deepEqual(Array.from(selected),[files[0],files[10]]);
      return [{indices:[0,1],ambiguous:false}];
    }
    assert.deepEqual(selected,files);
    return Array.from({length:10},(_,i)=>({indices:[i,i+10],ambiguous:i===0}));
  }};
  const context=vm.createContext({require:name=>name==='electron'?electron:name==='./local-images'?localImages:require(name),
    __dirname,process,console,URL,Response});
  vm.runInContext(await fs.readFile(path.join(__dirname,'main.js'),'utf8'),context);
  await initialized;
  const call=(name,data)=>invoke({sender:window.webContents},name,data);
  dialogs.push({canceled:true,filePaths:[]});
  assert.equal((await call('matchImages')).busy,false);
  dialogs.push({canceled:false,filePaths:files});
  let state=await call('matchImages');
  assert.equal(state.imageProducts.length,10);assert.equal(state.imageReviewRequired,true);
  assert.ok((await call('exportImages')).error.includes('Confirmar pares'));
  state=await call('swapImageSlots',{productId:state.imageProducts[0].id,scene:0,
    otherProductId:state.imageProducts[1].id,otherScene:1});
  assert.equal(state.imageProducts[0].images[0],'input-11.png');
  assert.equal(state.imageProducts[1].images[1],'input-0.png');
  fail=true;dialogs.push({canceled:false,filePaths:files});
  state=await call('matchImages');
  assert.ok(state.error);assert.equal(state.imageProducts.length,10);assert.equal(state.busy,false);
  await call('confirmImagePairs');
  dialogs.push({canceled:false,filePaths:[folder]});
  state=await call('exportImages');
  assert.equal(state.error,undefined);
  const output=(await fs.readdir(folder)).find(name=>name.startsWith('Imagens-Flow-'));
  const exported=await fs.readdir(path.join(folder,output));assert.equal(exported.length,20);
  const contents=await Promise.all(exported.map(async name=>(await fs.readFile(path.join(folder,output,name)))[0]));
  assert.deepEqual(contents.sort((a,b)=>a-b),Array.from({length:20},(_,i)=>i));
  fail=false;
  dialogs.push({canceled:false,filePaths:[files[0]]},{canceled:true,filePaths:[]});
  state=await call('matchSceneImages');
  assert.equal(state.imageProducts.length,10);assert.equal(state.busy,false);
  dialogs.push({canceled:false,filePaths:[files[0]]},{canceled:false,filePaths:[files[10],files[11]]});
  state=await call('matchSceneImages');assert.ok(state.error);assert.equal(state.imageProducts.length,10);
  dialogs.push({canceled:false,filePaths:[files[0]]},{canceled:false,filePaths:[files[10]]});
  state=await call('matchSceneImages');assert.equal(state.error,undefined);
  assert.equal(state.imageProducts.length,11);
  assert.equal(state.imageProducts[10].images[0],'input-0.png');
  assert.equal(state.imageProducts[10].images[1],'input-10.png');
  assert.equal(state.imageReviewRequired,true);
});
