
(function(){
  // The owner's preferences: the theme, the folds, which view opens. The server's copy
  // (state/prefs.json, drawn into the page) is the truth, because localStorage
  // belongs to one origin in one browser profile and the page is opened from
  // more than one. localStorage stays as the copy a page opened from disk uses.
  var P=window.deskPrefs||{};
  var KEY='__DESK_KEY__';
  var sendTimer=null,unsent={};
  function getPref(k){
    if(Object.prototype.hasOwnProperty.call(P,k))return P[k];
    try{return localStorage.getItem(k)}catch(e){return null}
  }
  function setPref(k,v){
    if(v==null)delete P[k];else P[k]=v;
    try{if(v==null)localStorage.removeItem(k);else localStorage.setItem(k,v)}catch(e){}
    unsent[k]=v==null?null:v;
    if(location.protocol!=='http:')return;
    // Batched, so shutting four folds in a row is one write, not four.
    clearTimeout(sendTimer);
    sendTimer=setTimeout(function(){
      var body=JSON.stringify(unsent);unsent={};
      fetch('/api/prefs?k='+encodeURIComponent(KEY),{method:'POST',headers:{'Content-Type':'application/json'},body:body})
        .catch(function(){});
    },400);
  }
  window.deskPref={get:getPref,set:setPref};
  // A browser that remembered things before the server did hands them over
  // once, so the first load after this change keeps their theme and their folds.
  try{
    for(var li=0;li<localStorage.length;li++){
      var lk=localStorage.key(li);
      if(lk&&(lk.indexOf('desk-')===0||lk.indexOf('fold-')===0)&&lk!=='desk-place'
         &&!Object.prototype.hasOwnProperty.call(P,lk))setPref(lk,localStorage.getItem(lk));
    }
  }catch(e){}
  var el=document.getElementById('countdown');
  var target=new Date(document.body.dataset.meeting);
  var what=(document.body.dataset.meetingLabel||'standup').toLowerCase();
  function tick(){
    var d=Math.floor((target-new Date())/1000);
    if(d<=0){el.textContent=what+' started';el.classList.add('soon');return}
    var h=Math.floor(d/3600),m=Math.floor(d%3600/60);
    el.textContent=(h?h+'h ':'')+m+'m to '+what;
    if(d<1800)el.classList.add('soon');
  }
  if(el&&!isNaN(target)){tick();setInterval(tick,20000)}
  function putPlain(s){
    // Slack only turns `foo` into code when the clipboard carries plain text,
    // so never let a rich-text flavour reach it.
    if(navigator.clipboard&&navigator.clipboard.writeText){
      return navigator.clipboard.writeText(s).catch(function(){legacy(s)});
    }
    legacy(s);
  }
  function legacy(s){
    var a=document.createElement('textarea');
    a.value=s;a.setAttribute('readonly','');
    a.style.cssText='position:fixed;top:-1000px';
    document.body.appendChild(a);a.select();
    try{document.execCommand('copy')}finally{document.body.removeChild(a)}
  }
  document.querySelectorAll('.copy').forEach(function(b){
    b.addEventListener('click',function(){
      putPlain(b.dataset.copy);
      var o=b.textContent;b.textContent='copied';
      setTimeout(function(){b.textContent=o},1200);
    });
  });
  // The button's own label is the client language's ("Japanese only",
  // "Script only"), so keep it rather than writing one in here.
  var t=document.getElementById('scriptonly');
  if(t){
    var only=t.textContent;
    t.addEventListener('click',function(){
      document.body.classList.toggle('script-only');
      t.textContent=document.body.classList.contains('script-only')
        ?'Show everything':only;
    });
  }

  // The view lives on the body, so CSS does the switching and nothing reloads.
  // A hash link from one view to the other lands on the right ticket because we
  // switch first and let the browser scroll after.
  var tabs=[].slice.call(document.querySelectorAll('.views button'));
  var where={};
  function show(view,remember){
    if(!tabs.length)return;
    var from=document.body.dataset.view;
    if(from&&from!==view)where[from]=window.scrollY;
    document.body.dataset.view=view;
    tabs.forEach(function(b){b.setAttribute('aria-selected',b.dataset.view===view)});
    if(remember!==false)try{sessionStorage.setItem('desk-view',view)}catch(e){}
    // Coming back to a view lands where you left it. Arriving for the first
    // time starts at the top, not halfway down because the other view was.
    if(from&&from!==view&&!location.hash)window.scrollTo(0,where[view]||0);
  }
  tabs.forEach(function(b){
    b.addEventListener('click',function(){show(b.dataset.view)});
  });
  // A job number on the milestone lands on the job itself, not the top of its
  // ticket, and opens it if it was folded. Without script it is a ticket link.
  document.addEventListener('click',function(e){
    var a=e.target.closest('a[data-job-link]');
    if(!a)return;
    var job=document.querySelector('.tk [data-job="'+a.dataset.jobLink+'"]');
    if(!job)return;
    e.preventDefault();
    show('desk');
    var fold=job.closest('details');
    while(fold){fold.open=true;fold=fold.parentElement&&fold.parentElement.closest('details')}
    job.scrollIntoView({behavior:'smooth',block:'start'});
    history.replaceState(null,'',a.getAttribute('href'));
  });
  document.addEventListener('click',function(e){
    var a=e.target.closest('a[data-goto]');
    if(!a)return;
    show(a.dataset.goto);
    var target=document.querySelector(a.getAttribute('href'));
    if(target){e.preventDefault();target.scrollIntoView({behavior:'smooth',block:'start'});
      history.replaceState(null,'',a.getAttribute('href'))}
  });
  // The finder. Everything on the page is in one index, so a ticket tag, an
  // item number or a word out of a title all land in the same place.
  var pal=document.getElementById('pal');
  var palIn=pal&&pal.querySelector('input');
  var palList=pal&&pal.querySelector('ul');
  var index=[];
  try{index=JSON.parse(document.getElementById('desk-index').textContent)}catch(e){}
  var hits=[],at=0;

  function jump(row){
    closePal();
    if(row.view)show(row.view);
    var el=document.getElementById(row.id);
    if(el){el.scrollIntoView({behavior:'smooth',block:'start'});
      history.replaceState(null,'','#'+row.id)}
  }
  function draw(){
    if(!hits.length){palList.innerHTML='<li class="p-none">Nothing matches</li>';return}
    // Text, never markup: the labels are ticket titles, which other people write.
    palList.textContent='';
    hits.forEach(function(r,i){
      var li=document.createElement('li');
      li.setAttribute('role','option');
      li.dataset.i=i;
      li.setAttribute('aria-selected',String(i===at));
      [['p-tag',r.tag],['p-t',r.label],['p-s',r.sub]].forEach(function(p){
        var s=document.createElement('span');
        s.className=p[0];
        s.textContent=p[1]==null?'':String(p[1]);
        li.appendChild(s);
      });
      palList.appendChild(li);
    });
  }
  function filter(q){
    q=(q||'').trim().toLowerCase();
    hits=q?index.filter(function(r){return r.hay.indexOf(q)>-1}).slice(0,12)
          :index.slice(0,12);
    at=0;draw();
  }
  function openPal(){
    if(!pal)return;
    pal.hidden=false;palIn.value='';filter('');palIn.focus();
  }
  function closePal(){if(pal)pal.hidden=true}
  if(pal){
    palIn.addEventListener('input',function(){filter(palIn.value)});
    palIn.addEventListener('keydown',function(e){
      if(e.key==='ArrowDown'){at=Math.min(at+1,hits.length-1);draw();e.preventDefault()}
      else if(e.key==='ArrowUp'){at=Math.max(at-1,0);draw();e.preventDefault()}
      else if(e.key==='Enter'&&hits[at]){jump(hits[at]);e.preventDefault()}
      else if(e.key==='Escape')closePal();
    });
    palList.addEventListener('click',function(e){
      var li=e.target.closest('li[data-i]');
      if(li&&hits[li.dataset.i])jump(hits[li.dataset.i]);
    });
    pal.addEventListener('click',function(e){if(e.target===pal)closePal()});
    [].forEach.call(document.querySelectorAll('[data-find]'),function(b){
      b.addEventListener('click',openPal);
    });
  }

  // A fold the owner shut stays shut. Reopening the page every hour and closing the
  // same three sections again is how a page stops being used.
  //
  // A fold that is drawn shut is the other way round: the owner opened it to look at
  // one thing, and remembering that forever is how The detail and the finished
  // work came back open on every page. So an open one is remembered against the
  // board it was opened on. A reload for an answer or a restart keeps it open;
  // a board that moved draws it shut again.
  var board=document.body.dataset.board||'';
  [].forEach.call(document.querySelectorAll('details[data-remember]'),function(d){
    var key='fold-'+d.dataset.remember;
    var shut=!d.open;
    var was=getPref(key);
    if(was==='0')d.open=false;
    else if(shut&&board&&was==='1@'+board)d.open=true;
    d.addEventListener('toggle',function(){
      setPref(key,!d.open?'0':shut?'1@'+board:'1');
    });
  });

  // The rest of the card's folds are not worth remembering forever, but they must
  // survive the reload an answer triggers: a section the owner opened to ask a question
  // should still be open when the answer lands, not snap back to its default. So
  // remember them for this tab only (sessionStorage clears on close, so a fresh
  // session still opens at the intended defaults), keyed by the card and the fold's
  // position within it so every card keeps its own state.
  var FOLDS='details.sub:not([data-remember]),details.qa-earlier';
  [].forEach.call(document.querySelectorAll(FOLDS),function(d){
    var card=d.closest('[id]');
    if(!card)return;
    var key='open-'+card.id+'-'+[].indexOf.call(card.querySelectorAll(FOLDS),d);
    try{var was=sessionStorage.getItem(key);if(was!==null)d.open=was==='1';}catch(e){}
    d.addEventListener('toggle',function(){
      try{sessionStorage.setItem(key,d.open?'1':'0')}catch(e){}
    });
  });

  // Keep the owner's place across that same reload, so the card they were reading
  // does not jump when the answer lands. By the card rather than by the pixel:
  // the job they were reading is found again and put back at the same height on screen,
  // because an answer, a fold or a new draft above it moves every pixel below.
  // Skipped when the URL points at an anchor, so a link to a ticket still wins.
  function placeKey(el){
    if(el.matches('article[id]'))return '#'+el.id;
    var a=el.querySelector('.ask[data-ask]');
    return a?'@'+a.dataset.ask:'';
  }
  function takePlace(){
    var best=null,first=null;
    [].forEach.call(document.querySelectorAll('article.tk[id],.act'),function(el){
      if(!el.offsetParent)return;
      var r=el.getBoundingClientRect();
      // The innermost thing crossing the top of the screen, so an item beats
      // the ticket it is in. Otherwise the first thing below the top.
      if(r.top<=1&&r.bottom>1){if(!best||best.contains(el))best=el}
      else if(r.top>1&&!first)first=el;
    });
    var el=best||first;
    var key=el&&placeKey(el);
    return key?{key:key,top:el.getBoundingClientRect().top,y:window.scrollY||0}:null;
  }
  function putPlace(p){
    if(!p)return;
    var el=null;
    if(p.key.charAt(0)==='#')el=document.getElementById(p.key.slice(1));
    else{
      var a=document.querySelector('.ask[data-ask="'+p.key.slice(1).replace(/"/g,'')+'"]');
      el=a&&a.closest('.act');
    }
    if(el&&el.offsetParent)window.scrollBy(0,el.getBoundingClientRect().top-p.top);
    else if(p.y)window.scrollTo(0,p.y);
  }
  // desk.js swaps one card's conversation in place, and the same promise holds
  // there: whatever the owner is reading stays where it is on screen.
  window.deskPlace={take:takePlace,put:putPlace};
  try{
    var sp=sessionStorage.getItem('desk-place');
    if(!location.hash&&sp)putPlace(JSON.parse(sp));
  }catch(e){}
  window.addEventListener('beforeunload',function(){
    try{sessionStorage.setItem('desk-place',JSON.stringify(takePlace()))}catch(e){}
  });

  // The ask box: the chat for one job. Opening it, and the way out for when the
  // page is a file on disk with no server behind it, which is the same words
  // with the job named, on the clipboard.
  function grow(t){
    // A line that grows with what the owner writes, up to a point, the way every chat
    // composer does. A fixed four-row box is either too big or too small.
    if(!t||t.tagName!=='TEXTAREA'||!t.closest('.ask,.chat-box'))return;
    t.style.height='auto';
    t.style.height=Math.min(t.scrollHeight+2,260)+'px';
  }
  document.addEventListener('input',function(e){grow(e.target)});
  function openAsk(box,seed){
    var panel=box.querySelector('.ask-box');
    var field=panel.querySelector('textarea');
    panel.hidden=false;
    box.querySelector('.ask-bar').hidden=true;
    if(seed&&!field.value)field.value=seed;
    grow(field);
    field.focus();
    field.selectionStart=field.selectionEnd=field.value.length;
    // Say which of the two this box is. A page with no server behind it can
    // only hand the words to a chat, and a copy button that looks like the
    // whole feature is worse than one that admits what it is.
    var send=panel.querySelector('.ask-send');
    var keys=panel.querySelector('.ask-keys');
    if(keys)keys.innerHTML=(send&&!send.hidden)
      ?'Enter sends · Shift+Enter for a new line · Esc closes'
      :'No desk server behind this page, so Enter copies it for a chat instead';
    return field;
  }
  function closeAsk(box){
    box.querySelector('.ask-box').hidden=true;
    box.querySelector('.ask-bar').hidden=false;
    box.querySelector('.ask-hint').textContent='';
  }
  document.addEventListener('click',function(e){
    // Rewrite this draft, from the draft. The box that does it is at the foot of
    // the card, so the button up here opens it with the sentence started.
    var here=e.target.closest('.ask-here');
    if(here){
      // The nearest of the three, so a button in a draft header opens that
      // job's box and one in a Say this header opens the speaking card's.
      var within=here.closest('.act-body,.tk,.st-tk');
      var target=within&&within.querySelector('.ask');
      if(target){
        var f=openAsk(target,here.dataset.askSeed||'');
        target.scrollIntoView({behavior:'smooth',block:'center'});
        f.focus();
      }
      return;
    }
    var box=e.target.closest('.ask');
    if(!box)return;
    // Two composers can be in one block: the box at the foot of the card, and
    // the reply line inside whichever answer the owner is pulling on. Every button
    // works on the one it is inside.
    var here=e.target.closest('.fu')||box.querySelector('.ask-box');
    // The side panel's conversation is an .ask with no box of its own: its
    // composer sits under it, and desk.js runs that one.
    if(!here)return;
    var field=here.querySelector('textarea');
    if(e.target.closest('.ask-open')){
      openAsk(box,'');
    }else if(e.target.closest('.ask-cancel')){
      closeAsk(box);
    }else if(e.target.closest('.ask-copy')){
      var q=(field.value||'').trim();
      // Following up in a chat means handing over what it is a follow-up to,
      // otherwise "why?" arrives on its own and means nothing.
      var lead=box.dataset.askLead||'';
      var about=e.target.closest('.qa');
      if(about){
        var asked=about.querySelector('.qa-q');
        var got=about.querySelector('.qa-a');
        lead+='\n\nThe owner is following up on this exchange.\n\nQ: '
          +(asked?asked.textContent.trim():'')+'\n\nA: '
          +(got?got.textContent.trim():'');
      }
      putPlain(lead+(q?'\n\n'+q:''));
      var hint=here.querySelector('.ask-hint')||box.querySelector('.ask-hint');
      hint.textContent=q?'Copied. Paste it into a chat on this folder.'
        :'Copied the job. Paste it into a chat and type what you want.';
    }
  });
  // Enter sends, the way it does in every chat. Shift-Enter is a new line, and
  // escape puts the box away, or lets go of the reply line.
  document.addEventListener('keydown',function(e){
    var panel=e.target.closest&&(e.target.closest('.ask-box')||e.target.closest('.fu'));
    if(!panel||e.target!==panel.querySelector('textarea'))return;
    var box=panel.closest('.ask');
    if(e.key==='Escape'){
      if(panel.classList.contains('fu')){
        e.target.value='';grow(e.target);e.target.blur();
      }else{closeAsk(box)}
      return;
    }
    if(e.key!=='Enter'||e.shiftKey||e.altKey||e.isComposing)return;
    e.preventDefault();
    var send=panel.querySelector('.ask-send');
    // No server behind the page, so the only thing Enter can honestly do is
    // hand the words over for a chat.
    (send&&!send.hidden?send:panel.querySelector('.ask-copy')).click();
  });

  var help=document.getElementById('help');
  function openHelp(){if(help&&!help.open)help.showModal()}
  if(help){
    [].forEach.call(document.querySelectorAll('[data-help]'),function(b){
      b.addEventListener('click',openHelp);
    });
    help.addEventListener('click',function(e){if(e.target===help)help.close()});
    var x=help.querySelector('.help-close');
    if(x)x.addEventListener('click',function(){help.close()});
  }

  // Settings: the theme and whether anything moves. Both are kept with the
  // prefs (see getPref at the top) and neither goes near the board. The theme is already on the body by now (set by the inline script in
  // the head, so the page never paints one theme and then another); this only
  // has to keep the buttons in step and write the choice down.
  var settings=document.getElementById('settings');
  if(settings){
    [].forEach.call(document.querySelectorAll('[data-settings]'),function(b){
      b.addEventListener('click',function(){if(!settings.open)settings.showModal()});
    });
    settings.addEventListener('click',function(e){
      if(e.target===settings)settings.close();
    });
    var sx=settings.querySelector('.help-close');
    if(sx)sx.addEventListener('click',function(){settings.close()});

    var themeBtns=[].slice.call(settings.querySelectorAll('[data-theme-set]'));
    var motionBtns=[].slice.call(settings.querySelectorAll('[data-motion-set]'));
    var openBtns=[].slice.call(settings.querySelectorAll('[data-open-set]'));
    function stored(key,fallback){return getPref(key)||fallback}
    function mark(){
      var now=document.body.dataset.theme||'plain';
      var moving=document.body.dataset.motion!=='off';
      var opening=stored('desk-open','auto');
      themeBtns.forEach(function(b){
        b.setAttribute('aria-pressed',b.dataset.themeSet===now);
      });
      motionBtns.forEach(function(b){
        b.setAttribute('aria-pressed',(b.dataset.motionSet==='on')===moving);
      });
      openBtns.forEach(function(b){
        b.setAttribute('aria-pressed',b.dataset.openSet===opening);
      });
    }
    openBtns.forEach(function(b){
      b.addEventListener('click',function(){
        setPref('desk-open',b.dataset.openSet);
        mark();
      });
    });

    // Forget the folds, the remembered tab and the scroll positions. Every one
    // of them is this browser's memory of how the owner left the page, so throwing
    // them away is a local act: it cannot touch the board.
    var forget=settings.querySelector('[data-forget-ui]');
    var forgetSaid=settings.querySelector('[data-forget-said]');
    if(forget)forget.addEventListener('click',function(){
      try{
        var keep={'desk-theme':1,'desk-motion':1,'desk-open':1};
        var drop=Object.keys(P);
        for(var i=0;i<localStorage.length;i++)drop.push(localStorage.key(i));
        drop.forEach(function(k){
          if(k&&!keep[k]&&(k.indexOf('desk-')===0||k.indexOf('fold-')===0))setPref(k,null);
        });
        sessionStorage.removeItem('desk-view');
        if(forgetSaid)forgetSaid.textContent='Forgotten. Reload to see it.';
      }catch(e){
        if(forgetSaid)forgetSaid.textContent='This browser will not let me.';
      }
    });
    themeBtns.forEach(function(b){
      b.addEventListener('click',function(){
        document.body.dataset.theme=b.dataset.themeSet;
        // The installed app's title bar follows the bar on the page.
        var tc=document.querySelector('meta[name=theme-color]');
        if(tc)tc.content=b.dataset.themeSet==='kraken'?'#29162f':'#0d1524';
        setPref('desk-theme',b.dataset.themeSet);
        mark();
      });
    });
    motionBtns.forEach(function(b){
      b.addEventListener('click',function(){
        var off=b.dataset.motionSet==='off';
        if(off)document.body.dataset.motion='off';
        else delete document.body.dataset.motion;
        setPref('desk-motion',off?'off':'on');
        mark();
      });
    });
    mark();
    document.addEventListener('keydown',function(e){
      if(e.key!==','||e.metaKey||e.ctrlKey||e.altKey)return;
      var t=e.target.tagName;
      if(t==='INPUT'||t==='TEXTAREA'||e.target.isContentEditable)return;
      e.preventDefault();
      if(!settings.open)settings.showModal();
    });
  }

  // The chip for the ticket you are looking at lights up as you scroll.
  var chips=[].slice.call(document.querySelectorAll('.jump a[href^="#t-"]'));
  if(chips.length&&'IntersectionObserver' in window){
    var seen={};
    var eye=new IntersectionObserver(function(rows){
      rows.forEach(function(r){seen[r.target.id]=r.isIntersecting});
      var live=chips.filter(function(a){return seen[a.getAttribute('href').slice(1)]});
      chips.forEach(function(a){a.classList.remove('on')});
      if(live.length)live[0].classList.add('on');
    },{rootMargin:'-90px 0px -70% 0px'});
    chips.forEach(function(a){
      var el=document.getElementById(a.getAttribute('href').slice(1));
      if(el)eye.observe(el);
    });
  }

  document.addEventListener('keydown',function(e){
    if((e.metaKey||e.ctrlKey)&&e.key==='k'){openPal();e.preventDefault();return}
    if(e.metaKey||e.ctrlKey||e.altKey)return;
    var tag=(e.target.tagName||'').toLowerCase();
    if(tag==='input'||tag==='textarea')return;
    if(e.key==='1')show('desk');
    if(e.key==='2')show('standup');
    if(e.key==='s'&&t)t.click();
    if(e.key==='/'){openPal();e.preventDefault()}
    if(e.key==='?')openHelp();
    if(e.key==='Escape')closePal();
  });
  if(tabs.length){
    // Which half to open on. The server decided by the clock; a preference in
    // Settings overrides that, and the tab the owner was last on overrides both,
    // because coming back to this page inside one session should not move them.
    var start=document.body.dataset.view;
    try{
      var pref=getPref('desk-open');
      if(pref==='desk'||pref==='standup')start=pref;
      var saved=sessionStorage.getItem('desk-view');
      if(saved)start=saved;
    }catch(e){}
    if(location.hash.indexOf('#s-')===0)start='standup';
    show(start,false);
  }
  // Scroll-spy: light the jump link for the section at the top of the view, so
  // the rail always says where you are down a long board. A top-line test rather
  // than a mid-band one, so a gap between sections never leaves nothing lit.
  (function(){
    [].forEach.call(document.querySelectorAll('.jump-track'),function(track){
      track.addEventListener('wheel',function(e){
        if(track.scrollWidth<=track.clientWidth)return;
        var d=Math.abs(e.deltaX)>Math.abs(e.deltaY)?e.deltaX:e.deltaY;
        if(!d)return;
        var prev=track.scrollLeft;
        track.scrollLeft+=d;
        if(track.scrollLeft!==prev)e.preventDefault();
      },{passive:false});
    });
    var nav=document.querySelector('nav.jump');
    if(!nav)return;
    var links=[].slice.call(nav.querySelectorAll('a[href^="#"]'));
    var map={},targets=[];
    links.forEach(function(a){
      var id=decodeURIComponent(a.getAttribute('href').slice(1));
      var el=document.getElementById(id);
      if(el){map[id]=a;targets.push(el);}
    });
    if(!targets.length)return;
    var ticking=false;
    function paint(){
      ticking=false;
      // Last section whose top has scrolled above the line is the one in view;
      // later in document order wins, so a ticket lights over its container.
      var line=140,current=targets[0].id;
      for(var i=0;i<targets.length;i++){
        if(targets[i].getBoundingClientRect().top-line<=0)current=targets[i].id;
      }
      links.forEach(function(a){a.classList.toggle('on',map[current]===a);});
    }
    function onScroll(){if(!ticking){ticking=true;requestAnimationFrame(paint);}}
    window.addEventListener('scroll',onScroll,{passive:true});
    window.addEventListener('resize',onScroll,{passive:true});
    paint();
  })();

  // The earlier half of a timeline, fetched when the owner asks for it.
  //
  // A card carries the last few days and a button for the rest, because 323
  // events was a quarter of a one-megabyte page and every one of them was
  // inside a fold that loads shut. This swaps the button for what comes back.
  //
  // Delegated from the document, so it works for every card without binding
  // ten handlers, and so it survives the reload that follows an ask.
  document.addEventListener('click',function(e){
    var b=e.target.closest('[data-events]');
    if(!b)return;
    var label=b.textContent;
    b.disabled=true;b.textContent='Loading…';
    fetch('/api/events?ref='+encodeURIComponent(b.dataset.events))
      .then(function(r){
        if(!r.ok)throw new Error(r.status);
        return r.text();
      })
      .then(function(html){
        if(!html){b.textContent='Nothing earlier';return}
        var box=document.createElement('div');
        box.innerHTML=html;
        // In front of the days already there, because the list reads forward.
        while(box.firstChild)b.parentNode.insertBefore(box.firstChild,b);
        b.remove();
      })
      .catch(function(){
        b.disabled=false;
        b.textContent=label;
        var say=document.createElement('span');
        say.className='ev-failed';
        say.textContent='Could not load those. They are in output/desk.md.';
        b.parentNode.insertBefore(say,b.nextSibling);
      });
  });

  // 波 — closing a job.
  //
  // Finishing something is the one moment on this desk with nothing to show for
  // it: `./tick.py 45` in a terminal reloads the page and the row just goes
  // quiet. So the page notices which number fell and marks it the way the work
  // itself is marked: a Hokusai wave crest with its claw of foam, and a 判子
  // seal pressed over it. Two and a bit seconds, once, then gone.
  //
  // It fires on a reload because that is when the board moves, and the set of
  // closed ids is compared against the one the last page carried. So it works
  // whether the item was closed in a terminal, by an ask, or by a sweep.
  (function(){
    var now=(document.body.dataset.closed||'').split(',').filter(Boolean);
    var KEY='desk-closed';
    var before=[];
    try{
      var raw=sessionStorage.getItem(KEY);
      if(raw!==null)before=raw.split(',').filter(Boolean);
      sessionStorage.setItem(KEY,now.join(','));
    }catch(e){return}
    // A close pressed on this page no longer reloads it, so desk.js plays the
    // wave itself and writes the close into the same record, or the next
    // reload would find it "new" and play it again an hour late.
    window.deskWave=function(id,on){
      id=String(id);
      var i=now.indexOf(id);
      if(on&&i<0)now.push(id);
      if(!on&&i>-1)now.splice(i,1);
      try{sessionStorage.setItem(KEY,now.join(','))}catch(e){}
      if(on)play([id]);
    };
    // Nothing to compare against on a first visit, so nothing is "new".
    if(!before.length)return;
    play(now.filter(function(id){return before.indexOf(id)<0}));
  })();
  function play(fresh){
    if(!fresh.length)return;
    if(matchMedia('(prefers-reduced-motion: reduce)').matches)return;
    if(document.body.dataset.motion==='off')return;
    var old=document.querySelector('.nami');
    if(old)old.remove();

    var wrap=document.createElement('div');
    wrap.className='nami';
    wrap.setAttribute('aria-hidden','true');
    // One crest, drawn once, in the indigo the rest of the page uses. The foam
    // circles are the 波の花 the prints break a wave into.
    wrap.innerHTML=
      '<svg viewBox="0 0 240 150" class="nami-w">'
      +'<path class="nami-b" d="M4 122c34 0 52-16 74-38C104 58 128 24 170 24c30 0 50 14 66 30"/>'
      +'<path class="nami-c" d="M170 24c-26 4-40 20-52 38 16-6 32-10 48-6-8 6-14 14-16 24 14-10 30-16 46-12-6-12-16-34-26-44z"/>'
      +'<g class="nami-f">'
      +'<circle cx="150" cy="44" r="6"/><circle cx="176" cy="34" r="4.5"/>'
      +'<circle cx="126" cy="62" r="4"/><circle cx="200" cy="46" r="3.5"/>'
      +'<circle cx="108" cy="80" r="3"/>'
      +'</g></svg>'
      +'<span class="nami-seal">済</span>'
      +'<span class="nami-said">'+(fresh.length>1
        ? fresh.length+' jobs closed'
        : 'Job '+fresh[0]+' closed')+'</span>';
    document.body.appendChild(wrap);
    // Taken off the page rather than left hidden, so nothing it drew can ever
    // sit in front of a click.
    setTimeout(function(){wrap.remove()},2600);
  }
})();
