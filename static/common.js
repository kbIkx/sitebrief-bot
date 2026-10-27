'use strict';
function el(tag, attrs={}, children=[]) {
  const node=document.createElement(tag);
  for(const [key,value] of Object.entries(attrs)) {
    if(key==='class') node.className=value;
    else if(key==='text') node.textContent=value;
    else if(key.startsWith('on')) node.addEventListener(key.slice(2),value);
    else if(value!==false && value!==null && value!==undefined) node.setAttribute(key,value===true?'':value);
  }
  for(const child of (Array.isArray(children)?children:[children])) if(child!==undefined && child!==null) node.append(child instanceof Node?child:document.createTextNode(String(child)));
  return node;
}
async function api(path, data, csrf) {
  const response=await fetch(path,{method:data===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-Requested-With':'SiteBrief',...(csrf?{'X-CSRF-Token':csrf}:{})},...(data===undefined?{}:{body:JSON.stringify(data)})});
  const result=await response.json();
  if(!response.ok) { const error=new Error(result.error||'Ошибка соединения'); error.status=response.status; throw error; }
  return result;
}
function link(label,url,className='') {return el('a',{href:url,target:'_blank',rel:'noopener noreferrer',class:className},label);}
