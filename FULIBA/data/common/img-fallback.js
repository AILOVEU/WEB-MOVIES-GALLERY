(function(){
  var ph='assets/img-placeholder.png';
  var s=document.currentScript;
  if(s&&s.src){ ph=s.src.replace(/[^\/]*$/,'')+'img-placeholder.png'; }
  function fix(t){
    if(!t||t.tagName!=='IMG'||t.getAttribute('data-fb'))return;
    t.setAttribute('data-fb','1');
    var cur=t.getAttribute('src')||'';
    t.setAttribute('title','图片未在本机：'+cur);
    t.classList.add('img-missing');
    if(cur.indexOf('img-placeholder.png')<0){ t.src=ph; }
  }
  /* error 不冒泡，用捕获阶段监听才能接住所有 <img>（含动态插入的） */
  document.addEventListener('error',function(e){fix(e.target);},true);
  function scan(){
    var im=document.images;
    for(var i=0;i<im.length;i++){ var t=im[i]; if(t.complete&&t.naturalWidth===0)fix(t); }
  }
  if(document.readyState==='loading'){ document.addEventListener('DOMContentLoaded',scan); }
  else{ scan(); }
  window.addEventListener('load',scan);
})();
