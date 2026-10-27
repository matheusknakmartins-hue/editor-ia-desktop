const fs=require('node:fs/promises');
const path=require('node:path');
const {spawn}=require('node:child_process');

function validatePairs(pairs,count){
  if(!Array.isArray(pairs)||pairs.length!==count/2)throw Error('O modelo não devolveu todos os pares.');
  const seen=new Set();
  for(const pair of pairs){
    if(!Array.isArray(pair.indices)||pair.indices.length!==2)throw Error('O modelo retornou um par inválido.');
    for(const index of pair.indices){
      if(!Number.isInteger(index)||index<0||index>=count||seen.has(index))throw Error('O modelo repetiu ou omitiu uma imagem.');
      seen.add(index);
    }
  }
  return pairs;
}

async function analyzeImages(files,{python,cache,temporaryRoot,onProgress}){
  if(files.length<2||files.length>20||files.length%2)throw Error('Selecione uma quantidade par de imagens, de 2 a 20.');
  const folder=await fs.mkdtemp(path.join(temporaryRoot,'image-pairing-'));
  try{
    const input=path.join(folder,'input.json'),output=path.join(folder,'output.json');
    await fs.writeFile(input,JSON.stringify(files));
    await new Promise((resolve,reject)=>{
      const child=spawn(python,[path.join(__dirname,'worker','pair_images.py'),'--input',input,'--output',output,'--cache',cache],
        {windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PYTHONIOENCODING:'utf-8'}});
      let errors='',buffer='',timedOut=false;
      const timer=setTimeout(()=>{timedOut=true;child.kill();},15*60*1000);
      child.stdout.setEncoding('utf8');child.stderr.setEncoding('utf8');
      child.stdout.on('data',chunk=>{
        buffer+=chunk;
        const lines=buffer.split(/\r?\n/);buffer=lines.pop();
        for(const line of lines)if(line)onProgress?.(line);
      });
      child.stderr.on('data',chunk=>{errors=(errors+chunk).slice(-6000);});
      child.on('error',error=>{clearTimeout(timer);reject(error);});
      child.on('close',code=>{
        clearTimeout(timer);
        if(timedOut)reject(Error('A análise local ultrapassou 15 minutos. Tente um lote menor.'));
        else if(code!==0)reject(Error(errors||`Análise local encerrou com código ${code}.`));
        else resolve();
      });
    });
    return validatePairs(JSON.parse(await fs.readFile(output,'utf8')),files.length);
  }finally{await fs.rm(folder,{recursive:true,force:true});}
}
module.exports={analyzeImages,validatePairs};
