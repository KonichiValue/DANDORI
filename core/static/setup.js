(function(){
  // The setup page. It asks the server where each step stands every couple of
  // seconds and redraws the steps from that, so a step finished in Terminal
  // turns green here on its own. Every button is a POST with the page's key;
  // a restarted server has a new key, and the boot id below is how the page
  // notices and reloads itself to pick it up.
  var S=window.SETUP||{};
  var KEY=S.key,BOOT=S.boot;
  var busy=false,refreshing=false,timer=null,lastWork='';
  var $=function(sel,root){return (root||document).querySelector(sel)};
  var $$=function(sel,root){return Array.prototype.slice.call((root||document).querySelectorAll(sel))};
  function q(path){return path+(path.indexOf('?')<0?'?':'&')+'k='+encodeURIComponent(KEY)}
  function post(path,body){
    return fetch(q(path),{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify(body||{})}).then(function(r){
        return r.json().catch(function(){return {}}).then(function(j){j._code=r.status;return j});
      });
  }

  // ---------------------------------------------------------- the steps
  function paint(st){
    var steps=st.steps||[],done=0,next=null;
    steps.forEach(function(s){
      var li=document.getElementById('step-'+s.id);
      if(!li)return;
      li.dataset.state=s.state;
      $('.st-said',li).textContent=s.said||'';
      if(s.state==='done')done++;
      if(!next&&s.state==='todo')next=li;
      if(s.mcps){
        var chips=$('.chips',li),html='';
        Object.keys(s.mcps).forEach(function(n){
          var v=s.mcps[n],word={connected:'connected','needs-auth':'needs approving',missing:'not added',failed:'failed'}[v]||v;
          html+='<span class="chip" data-s="'+v+'">'+n.charAt(0).toUpperCase()+n.slice(1)+': '+word+'</span>';
        });
        chips.innerHTML=html;
      }
      if(s.gaps){$('.st-said',li).title=s.gaps.join(', ')}
    });
    $$('.step').forEach(function(li){li.classList.toggle('next',li===next)});
    $('.su-done').textContent=done;
    $('.su-fill').style.width=Math.round(100*done/Math.max(steps.length,1))+'%';
    $('.su-at').textContent=st.checked_at?'checked '+st.checked_at:'';
    // The answers step opens itself while it is the next thing to do.
    var ans=document.getElementById('step-answers');
    if(ans&&next===ans&&!ans.dataset.seen){ans.dataset.seen='1';openForm(true)}
    var ready=st.ready;
    $$('[data-desk]').forEach(function(el){el.hidden=!ready});
    var rf=$('[data-refresh]');
    if(rf)rf.hidden=!!ready;
    paintWork(st.work||{});
  }

  function paintWork(w){
    if(!w.action){return}
    var li=document.querySelector('[data-run="'+w.action+'"]');
    li=li&&li.closest('.step');
    if(!li)return;
    var box=$('.st-run',li);
    box.hidden=false;
    box.dataset.s=w.state;
    $('.run-said',box).textContent=w.said||'';
    var link=$('.run-url',box);
    link.hidden=!w.url;
    if(w.url)link.href=w.url;
    var log=(w.lines||[]).join('\n');
    if(log!==lastWork){
      lastWork=log;
      var pre=$('.run-log',box);
      pre.textContent=log;
      pre.scrollTop=pre.scrollHeight;
    }
    busy=w.state==='running';
    $$('[data-run]').forEach(function(b){
      if(b.dataset.run!=='recheck')b.disabled=busy&&b.dataset.run===w.action;
    });
  }

  function steps(){
    return fetch(q('/api/setup/state')).then(function(r){return r.json()}).then(paint)
      .catch(function(){});
  }

  function poll(){
    clearTimeout(timer);
    fetch('/api/status').then(function(r){return r.json()}).then(function(s){
      if(s.boot&&BOOT&&s.boot!==BOOT){location.reload();return null}
      if(s.state==='running'&&s.kind==='sweep')refreshing=true;
      if(refreshing)paintRefresh(s);
      return steps();
    }).catch(function(){
      // A server mid-restart answers nothing for a second or two. Keep asking.
    }).then(function(){
      timer=setTimeout(poll,busy||refreshing?1500:3000);
    }).catch(function(){});
  }

  function paintRefresh(s){
    var li=document.getElementById('step-refresh'),box=$('.st-run',li);
    box.hidden=false;
    box.dataset.s=s.state==='running'?'running':(s.state==='failed'||s.state==='needs_login')?'failed':'done';
    var said=s.message||'Starting';
    if(s.state==='running'&&s.last)said=s.last+(s.steps?' ('+s.steps+' steps)':'');
    $('.run-said',box).textContent=said;
    var link=$('.run-url',box);
    link.hidden=!s.url;
    if(s.url)link.href=s.url;
    if(s.state!=='running'){refreshing=false;$('[data-refresh]').disabled=false}
  }

  // ---------------------------------------------------------- the buttons
  document.addEventListener('click',function(e){
    var run=e.target.closest('[data-run]');
    if(run){
      e.preventDefault();
      var action=run.dataset.run;
      if(action==='recheck'){
        $$('.step').forEach(function(li){
          if(['claude','gateway','connect'].indexOf(li.dataset.step)>=0){
            li.dataset.state='checking';$('.st-said',li).textContent='Checking';
          }
        });
      }
      run.disabled=true;
      post('/api/setup/run',{action:action}).then(function(j){
        run.disabled=false;
        if(j._code>=400){alert(j.error||'That did not start.');return}
        poll();
      }).catch(function(){run.disabled=false;poll()});
      return;
    }
    if(e.target.closest('[data-open-form]')){e.preventDefault();openForm()}
    var rf=e.target.closest('[data-refresh]');
    if(rf){
      e.preventDefault();
      rf.disabled=true;refreshing=true;
      post('/api/refresh').then(function(j){
        if(j._code>=400&&j._code!==409){refreshing=false;rf.disabled=false;alert(j.error||j.message||'The refresh did not start.')}
        poll();
      }).catch(function(){refreshing=false;rf.disabled=false});
      return;
    }
    var add=e.target.closest('[data-add]');
    if(add){
      var tpl=document.getElementById('tpl-'+add.dataset.add);
      var rows=document.querySelector('[data-rows="'+add.dataset.add+'"]');
      rows.insertAdjacentHTML('beforeend',tpl.innerHTML);
      var first=rows.lastElementChild.querySelector('input');
      if(first)first.focus();
      return;
    }
    var drop=e.target.closest('[data-drop]');
    if(drop){drop.closest('.row').remove()}
    var step=e.target.closest('.step[data-state="done"] .st-top');
    if(step){step.closest('.step').classList.toggle('open')}
  });

  function openForm(quiet){
    var wrap=$('#step-answers .form-wrap');
    wrap.hidden=false;
    $('#step-answers').classList.add('open');
    if(!quiet){var f=$('#answers input');if(f)f.focus()}
  }

  document.addEventListener('change',function(e){
    var t=e.target;
    if(t.name==='client.language'){$('.lang-more').hidden=t.value==='en'}
    if(t.dataset&&t.dataset.opens){
      var more=document.querySelector('[data-more="'+t.dataset.opens+'"]');
      if(more)more.hidden=!t.checked;
    }
  });

  // Auto-derive short name, terminal command and default standup title from
  // the client name until the owner edits those fields directly.
  var shortEl=$('input[name="client.short"]'),cmdEl=$('input[name="desk.command"]'),
      meetEl=$('input[name="meeting.name"]');
  [shortEl,cmdEl,meetEl].forEach(function(el){
    if(el&&el.value)el.dataset.touched='1';
    if(el)el.addEventListener('input',function(){el.dataset.touched='1'});
  });
  var cnameEl=$('input[name="client.name"]');
  if(cnameEl)cnameEl.addEventListener('input',function(){
    var words=cnameEl.value.trim().split(/\s+/).filter(Boolean);
    var initials=words.length>1
      ?words.map(function(w){return w.charAt(0)}).join('').toUpperCase().slice(0,4)
      :(words[0]||'').replace(/[^A-Za-z]/g,'').toUpperCase().slice(0,4);
    if(shortEl&&!shortEl.dataset.touched)shortEl.value=initials;
    if(cmdEl&&(!cmdEl.dataset.touched||cmdEl.value==='DANDORI')&&initials){
      cmdEl.value=initials.toLowerCase();
    }
    if(meetEl&&!meetEl.dataset.touched&&initials){
      meetEl.value=initials+' Standup';
    }
  });

  // ---------------------------------------------------------- the answers
  function gather(form){
    var out={};
    $$('input[name],select[name]',form).forEach(function(el){
      if(el.closest('[data-row]'))return;
      if(el.type==='checkbox'){
        if(el.name==='meeting.days'){
          out[el.name]=out[el.name]||[];
          if(el.checked)out[el.name].push(el.value);
        }else out[el.name]=el.checked;
        return;
      }
      out[el.name]=el.value;
    });
    out['asana.projects']=$$('[data-row="project"]',form).map(function(row){
      return {url:$('[data-k="url"]',row).value,name:$('[data-k="name"]',row).value};
    });
    out['meeting.also']=$$('[data-row="room"]',form).map(function(row){
      return {
        name:$('[data-k="name"]',row).value,
        time_local:$('[data-k="time_local"]',row).value,
        kind:$('[data-k="kind"]',row).value,
        days:$$('input[type=checkbox]:checked',row).map(function(b){return b.value})
      };
    });
    return out;
  }

  var form=document.getElementById('answers');
  if(form)form.addEventListener('submit',function(e){
    e.preventDefault();
    var said=$('.save-said'),btn=$('.save-bar .btn');
    $$('.f.bad',form).forEach(function(f){f.classList.remove('bad')});
    $$('.f-e',form).forEach(function(f){f.textContent=''});
    said.className='save-said';said.textContent='Saving';btn.disabled=true;
    post('/api/setup/save',gather(form)).then(function(j){
      btn.disabled=false;
      if(j.errors){
        var first=null;
        Object.keys(j.errors).forEach(function(k){
          var box=form.querySelector('[data-for="'+k+'"]');
          if(!box)return;
          box.classList.add('bad');
          var adv=box.closest('details.adv');
          if(adv)adv.open=true;
          var err=box.classList.contains('f-e')?box:$('.f-e',box);
          if(err)err.textContent=j.errors[k];
          first=first||box;
        });
        said.className='save-said bad';
        said.textContent=Object.keys(j.errors).length+' to fix, marked in red.';
        if(first)first.scrollIntoView({behavior:'smooth',block:'center'});
        return;
      }
      if(j._code>=400){said.className='save-said bad';said.textContent=j.error||'Not saved.';return}
      said.className='save-said ok';
      said.textContent='Saved.';
      $('#step-answers').classList.remove('open');
      $('#step-answers .form-wrap').hidden=true;
      if(j.restart){
        // The region, port and timezone are read when the server starts.
        said.textContent='Saved. Restarting the page to use them.';
        post('/api/setup/run',{action:'restart'}).catch(function(){});
        if(String(j.port)!==location.port){
          setTimeout(function(){location.href='http://127.0.0.1:'+j.port+'/setup'},5000);
        }
      }
      poll();
    }).catch(function(){
      btn.disabled=false;said.className='save-said bad';said.textContent='The page server did not answer. Try again.';
    });
  });

  poll();
})();
