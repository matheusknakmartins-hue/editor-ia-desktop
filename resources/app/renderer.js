const $=id=>document.getElementById(id);
let state={groups:[],busy:false},selected=null;
async function call(name,data){try{const result=await window.editor.call(name,data);render(result);if(result.error)alert(result.error);return result;}catch(e){$('status').textContent=e.message;}}
function render(next){
  state=next;
  if(!state.groups.some(g=>g.id===selected))selected=state.groups[0]?.id||null;
  $('groups').replaceChildren();
  for(const g of state.groups){
    const button=document.createElement('button');button.className='product'+(g.id===selected?' selected':'');
    const name=document.createElement('strong');name.textContent=g.name;
    const message=document.createElement('small');message.textContent=g.message;button.append(name,message);
    button.onclick=()=>{selected=g.id;render(state);};$('groups').append(button);
  }
  const g=state.groups.find(g=>g.id===selected);
  $('details').hidden=!g;$('empty').hidden=!!g;$('title').textContent=g?.name||'Prévia';
  $('count').textContent=`${state.groups.filter(g=>g.output).length} prontos`;
  if(g){$('clips').textContent=g.clips.map((c,i)=>`${i+1}. ${c}`).join(' · ');g.speeds.forEach((s,i)=>{if(document.activeElement!==$('s'+i))$('s'+i).value=s;});}
  const url=g?.url||'';
  if(($('video').getAttribute('src')||'')!==url){if(url)$('video').src=url;else{$('video').removeAttribute('src');$('video').load();}}
  document.querySelectorAll('button:not(.product),input[type=number]').forEach(b=>b.disabled=state.busy);
  $('export').disabled=state.busy||!g?.output;$('exportAll').disabled=state.busy||!state.groups.some(g=>g.output);
  $('keyState').textContent=state.hasKey?'Chave salva e protegida no Windows.':'Nenhuma chave salva.';
  $('status').textContent=state.status;
  const products=state.imageProducts||[];
  $('imageCount').textContent=`${products.length} produtos · ${products.length*2} imagens`;
  $('imageProducts').replaceChildren();
  for(const product of products){
    const row=document.createElement('div');row.className='image-product';
    const title=document.createElement('strong');title.textContent=product.name;row.append(title);
    product.images.forEach((file,index)=>{
      const description=document.createElement('p');
      description.textContent=`${index?'Cenas 2/3':'Cena 1'}: ${file} → ${product.filenames[index]}`;
      row.append(description);
    });
    const remove=document.createElement('button');remove.textContent='Remover produto';remove.disabled=state.busy;
    remove.onclick=()=>{if(confirm(`Remover ${product.name} das imagens? Os originais serão preservados.`))call('removeImages',product.id);};
    row.append(remove);$('imageProducts').append(row);
  }
  $('exportImages').disabled=state.busy||!products.length;
}
$('add').onclick=()=>call('add');
$('settings').onclick=()=>$('config').showModal();
$('closeConfig').onclick=()=>$('config').close();
$('saveKey').onclick=async()=>{const result=await call('key',$('key').value);if(result&&!result.error)$('key').value='';};
$('forgetKey').onclick=()=>{if(confirm('Remover a chave salva neste computador?'))call('forgetKey');};
$('remove').onclick=()=>{if(confirm('Remover este produto da fila? Seus arquivos serão preservados.'))call('remove',selected);};
for(let i=0;i<3;i++)$('s'+i).onchange=()=>call('speeds',{id:selected,values:[0,1,2].map(j=>Number($('s'+j).value))});
$('generate').onclick=()=>call('generate',{id:selected,assembly:$('assembly').checked});
$('generateAll').onclick=()=>call('generate',{assembly:$('assembly').checked});
$('export').onclick=()=>call('export',selected);
$('exportAll').onclick=()=>call('export');
$('addImages').onclick=()=>call('addImages');
$('exportImages').onclick=()=>call('exportImages');
window.editor.updates(render);call('state');
