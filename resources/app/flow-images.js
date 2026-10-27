const fs=require('node:fs/promises');
const path=require('node:path');
const {constants}=require('node:fs');

const imageExtensions=['png','jpg','jpeg','webp'];
function imagePlan(products){
  if(!Array.isArray(products)||!products.length)throw Error('Adicione um produto com duas imagens.');
  const width=Math.max(3,String(products.length*2).length);
  products.forEach(product=>{
    if(!Array.isArray(product.images)||product.images.length!==2)throw Error('Cada produto precisa de duas imagens.');
  });
  return [0,1].flatMap(scene=>products.map((product,index)=>{
      const source=product.images[scene];
      const extension=path.extname(source).toLowerCase();
      if(!imageExtensions.includes(extension.slice(1)))throw Error('Use imagens PNG, JPG ou WebP.');
      const sequence=String(scene*products.length+index+1).padStart(width,'0');
      const number=String(index+1).padStart(2,'0');
      return {source,productIndex:index,name:`${sequence}_${scene?'Cenas-2-3':'Cena-1'}_Prod-${number}${extension}`};
  }));
}
async function exportImages(products,destination){
  const plan=imagePlan(products);
  for(const item of plan){
    if(!(await fs.stat(item.source)).isFile())throw Error('Uma das imagens não é um arquivo.');
  }
  // Each export gets its own directory, so existing exports are never overwritten.
  const directory=await fs.mkdtemp(path.join(destination,'Imagens-Flow-'));
  try{
    for(const item of plan)await fs.copyFile(item.source,path.join(directory,item.name),constants.COPYFILE_EXCL);
    return {directory,count:plan.length};
  }catch(error){
    await fs.rm(directory,{recursive:true,force:true});
    throw error;
  }
}
module.exports={imageExtensions,imagePlan,exportImages};
