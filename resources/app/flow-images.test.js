const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs/promises');
const os=require('node:os');
const path=require('node:path');
const {imagePlan,exportImages}=require('./flow-images');

test('exports scenes into separate folders, preserving names, bytes, extensions and product correspondence',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'flow-images-test-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  const sources=['one.PNG','two.jpg','three.webp','four.jpeg'].map(name=>path.join(root,name));
  for(const [index,file] of sources.entries())await fs.writeFile(file,Buffer.from([0,index,255]));
  const products=[{images:sources.slice(0,2)},{images:sources.slice(2)}];
  const names=['001_Cena-1_Prod-01.png','002_Cena-1_Prod-02.webp',
    '003_Cenas-2-3_Prod-01.jpg','004_Cenas-2-3_Prod-02.jpeg'];
  assert.deepEqual(imagePlan(products).map(item=>item.name),names);
  const first=await exportImages(products,root);
  assert.equal(first.count,4);
  for(const item of imagePlan(products)){
    assert.deepEqual(await fs.readFile(path.join(first.directory,item.folder,item.name)),await fs.readFile(item.source));
  }
  const second=await exportImages(products,root);
  assert.notEqual(second.directory,first.directory);
  assert.deepEqual((await fs.readdir(first.directory)).sort(),['Cena-1','Cenas-2-3']);
  assert.deepEqual(await fs.readdir(path.join(first.directory,'Cena-1')),names.slice(0,2));
  assert.deepEqual(await fs.readdir(path.join(first.directory,'Cenas-2-3')),names.slice(2));
});

test('rejects incomplete products and unsupported images',()=>{
  for(const products of [[],[{images:['one.png']}],[{images:['one.exe','two.jpg']}]] ){
    assert.throws(()=>imagePlan(products));
  }
});

test('missing source leaves the destination unchanged',async t=>{
  const root=await fs.mkdtemp(path.join(os.tmpdir(),'flow-images-missing-'));
  t.after(()=>fs.rm(root,{recursive:true,force:true}));
  await assert.rejects(exportImages([{images:[path.join(root,'missing.png'),path.join(root,'missing.jpg')]}],root));
  assert.deepEqual(await fs.readdir(root),[]);
});

test('number padding keeps lexical order for large batches and removed products are renumbered',()=>{
  const products=Array.from({length:501},()=>({images:['one.png','two.png']}));
  const names=imagePlan(products).map(item=>item.name);
  assert.deepEqual([...names].sort(),names);
  assert.equal(names[0],'0001_Cena-1_Prod-01.png');
  assert.equal(names[501],'0502_Cenas-2-3_Prod-01.png');
  assert.equal(imagePlan(products.slice(1,3))[0].name,'001_Cena-1_Prod-01.png');
});
