// ============================================================
// 认证
// ============================================================
function showAuthError(m){const e=document.getElementById('authError');e.textContent=m;e.style.display='block';}
function hideAuthError(){document.getElementById('authError').style.display='none';}
function showLogin(){hideAuthError();document.getElementById('loginForm').style.display='block';document.getElementById('registerForm').style.display='none';}
function showRegister(){hideAuthError();document.getElementById('loginForm').style.display='none';document.getElementById('registerForm').style.display='block';}
async function doLogin(){const u=document.getElementById('loginUsername').value.trim(),p=document.getElementById('loginPassword').value;if(!u||!p){showAuthError('请输入用户名和密码');return;}try{const r=await fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const d=await r.json();if(!r.ok){showAuthError(d.error||'登录失败');return;}if(d.csrf_token)csrfToken=d.csrf_token;enterApp(d.user);}catch(e){showAuthError('网络错误: '+e.message);}}
async function doRegister(){const u=document.getElementById('regUsername').value.trim(),p=document.getElementById('regPassword').value,p2=document.getElementById('regPassword2').value;if(!u||!p){showAuthError('请输入用户名和密码');return;}if(p!==p2){showAuthError('两次密码不一致');return;}try{const r=await fetch('/api/register',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({username:u,password:p})});const d=await r.json();if(!r.ok){showAuthError(d.error||'注册失败');return;}if(d.csrf_token)csrfToken=d.csrf_token;enterApp(d.user);}catch(e){showAuthError('网络错误: '+e.message);}}
async function doLogout(){await fetch('/api/logout',{method:'POST'});document.getElementById('authPage').style.display='block';document.getElementById('appPage').style.display='none';showLogin();}
function enterApp(u){document.getElementById('authPage').style.display='none';document.getElementById('appPage').style.display='block';document.getElementById('headerUsername').textContent=u.username;document.getElementById('dashGreeting').textContent=u.username+'，你好';loadDashboard();}
async function checkAuth(){try{const r=await fetch('/api/me');const d=await r.json();if(d.logged_in){if(d.csrf_token)csrfToken=d.csrf_token;enterApp(d.user);}else{document.getElementById('authPage').style.display='block';document.getElementById('appPage').style.display='none';}}catch(e){document.getElementById('authPage').style.display='block';document.getElementById('appPage').style.display='none';}}
document.getElementById('loginPassword').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
document.getElementById('loginUsername').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin();});
document.getElementById('regPassword2').addEventListener('keydown',e=>{if(e.key==='Enter')doRegister();});
checkAuth();
let csrfToken='';
function _headers(h={}){if(csrfToken)h['X-CSRF-Token']=csrfToken;return h;}
async function authFetch(u,o={}){o.headers=_headers(o.headers||{});const r=await fetch(u,o);if(r.status===401){doLogout();throw new Error('登录已过期');}return r;}

// ============================================================
// 页面切换
// ============================================================
let currentPage='dashboard';
function switchPage(name){
    currentPage=name;
    document.querySelectorAll('.page').forEach(p=>p.classList.remove('active'));
    document.querySelectorAll('.nav-item').forEach(n=>n.classList.remove('active'));
    document.getElementById('page-'+name).classList.add('active');
    document.getElementById('nav-'+name).classList.add('active');
    if(name==='dashboard')loadDashboard();
    if(name==='modelConfig'){loadPresets();loadModelConfig();}
    if(name==='testpoints'){loadTpMaterials();loadTpHistory();}
    if(name==='generator'){loadGenMaterials();loadGenTestPoints();}
    if(name==='materials')loadMaterials();
}

// ============================================================
// 测试点生成
// ============================================================
let testPointsData = [];
let tpUploadedFiles = [];
let tpSelectedMats = new Set();

async function loadTpMaterials() {
    try {
        const r = await authFetch('/api/materials');
        const d = await r.json();
        const mats = d.materials || [];
        const sel = document.getElementById('tpMatSelect');
        const list = document.getElementById('tpMatList');
        if (!mats.length) { sel.style.display = 'none'; return; }
        sel.style.display = 'block';
        tpSelectedMats.clear();
        list.innerHTML = mats.map(m => `
            <label style="display:flex;align-items:center;gap:4px;padding:4px 8px;border:1px solid #ddd;border-radius:5px;font-size:11px;cursor:pointer;">
                <input type="checkbox" value="${m.id}" onchange="tpToggleMat(${m.id})" style="accent-color:#1a73e8;">
                ${esc(m.title)}
            </label>
        `).join('');
    } catch (e) {}
}
function tpToggleMat(id) { if (tpSelectedMats.has(id)) tpSelectedMats.delete(id); else tpSelectedMats.add(id); }
const tpUploadArea = document.getElementById('tpUploadArea');
const tpFileInput = document.getElementById('tpFileInput');
const tpFileList = document.getElementById('tpFileList');
tpUploadArea.addEventListener('click', () => tpFileInput.click());
tpUploadArea.addEventListener('dragover', e => { e.preventDefault(); tpUploadArea.classList.add('dragover'); });
tpUploadArea.addEventListener('dragleave', () => tpUploadArea.classList.remove('dragover'));
tpUploadArea.addEventListener('drop', e => { e.preventDefault(); tpUploadArea.classList.remove('dragover'); tpAddFiles(e.dataTransfer.files); });
tpFileInput.addEventListener('change', () => { tpAddFiles(tpFileInput.files); tpFileInput.value = ''; });
document.getElementById('tpRequirement').addEventListener('paste', e => {
    const items = e.clipboardData?.items;
    if (!items) return;
    for (const item of items) {
        if (item.type.startsWith('image/')) {
            e.preventDefault();
            const f = item.getAsFile();
            if (f) { const ext = f.type.split('/')[1] || 'png'; tpUploadedFiles.push(new File([f], `粘贴_${Date.now()}.${ext}`, { type: f.type })); tpRenderFileList(); showToast('已添加图片'); }
        }
    }
});
function tpAddFiles(fs) { for (const f of fs) { if (tpUploadedFiles.some(x => x.name === f.name && x.size === f.size)) continue; tpUploadedFiles.push(f); } tpRenderFileList(); }
function tpRemoveFile(i) { tpUploadedFiles.splice(i, 1); tpRenderFileList(); }
function tpRenderFileList() {
    if (!tpUploadedFiles.length) { tpFileList.innerHTML = ''; return; }
    tpFileList.innerHTML = tpUploadedFiles.map((f, i) => {
        const isImg = /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name);
        const ext = f.name.split('.').pop().toUpperCase();
        const pre = isImg ? `<img src="${URL.createObjectURL(f)}">` : '';
        return `<div class="file-item">${pre}<div><div class="fn" title="${esc(f.name)}">${esc(f.name)}</div><span class="ft">${isImg ? '图片' : ext}</span></div><button class="rb" onclick="tpRemoveFile(${i})">&times;</button></div>`;
    }).join('');
}
async function generatePoints(){
    if(isGenerating){showToast('正在生成中，请勿重复点击','error');return;}
    const req=document.getElementById('tpRequirement').value.trim();
    if(!req&&!tpUploadedFiles.length){showToast('请输入需求描述或上传图片','error');return;}
    isGenerating=true;
    const btn=document.getElementById('btnGenPoints');btn.disabled=true;btn.innerHTML='<span class="spinner"></span> 生成中...';
    document.getElementById('tpLoading').style.display='block';document.getElementById('tpLoadingText').textContent='正在分析需求...';
    document.getElementById('tpResult').style.display='none';
    try{
        const fd=new FormData();fd.append('requirement',req);
        if(tpSelectedMats.size>0)fd.append('material_ids',[...tpSelectedMats].join(','));
        for(const f of tpUploadedFiles)fd.append('files',f);
        const resp=await fetch('/api/generate-points',{method:'POST',body:fd});
        if(resp.status===401){doLogout();throw new Error('登录已过期');}
        const rd=resp.body.getReader(),dec=new TextDecoder();let buf='';
        while(true){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();for(const line of lines){if(!line.startsWith('data: '))continue;try{const ev=JSON.parse(line.slice(6));if(ev.type==='progress')document.getElementById('tpLoadingText').textContent=ev.message;else if(ev.type==='done'){testPointsData=ev.data.points||[];renderTestPoints(testPointsData);showToast(`生成完成！共 ${ev.data.total} 个测试点`);}else if(ev.type==='error')throw new Error(ev.message);}catch(e){if(e.message&&!e.message.includes('JSON'))throw e;}}}
    }catch(e){showToast(e.message,'error');}
    finally{isGenerating=false;btn.disabled=false;btn.innerHTML='&#128209; 生成测试点';document.getElementById('tpLoading').style.display='none';}
}
function renderTestPoints(points){
    const total=points.reduce((s,m)=>s+m.points.length,0);
    document.getElementById('tpTotal').textContent=total;
    document.getElementById('tpModules').textContent=points.length;
    const tree=document.getElementById('tpTree');
    tree.innerHTML=points.map((m,i)=>`
        <div class="tp-module">
            <div class="tp-module-title" onclick="toggleTpModule(${i})">
                <span class="arrow" id="tpArrow${i}">&#9656;</span>
                ${esc(m.module)}
                <span class="tp-module-count">${m.points.length} 个测试点</span>
            </div>
            <div class="tp-points" id="tpPoints${i}">
                ${m.points.map(p=>`<div class="tp-point"><div class="tp-dot"></div><div class="tp-info"><div class="tp-title">${esc(p.title)}</div>${p.description?`<div class="tp-desc">${esc(p.description)}</div>`:''}</div></div>`).join('')}
            </div>
        </div>
    `).join('');
    document.getElementById('tpResult').style.display='block';
}
function toggleTpModule(i){
    const pts=document.getElementById('tpPoints'+i);
    const arrow=document.getElementById('tpArrow'+i);
    pts.classList.toggle('show');
    arrow.classList.toggle('open');
}
async function exportPoints(fmt){
    if(!testPointsData.length){showToast('没有测试点可导出','error');return;}
    const req=document.getElementById('tpRequirement').value.trim()||'测试点';
    try{
        const r=await authFetch('/api/export-points',{
            method:'POST',headers:{'Content-Type':'application/json'},
            body:JSON.stringify({points:testPointsData,format:fmt,title:req.substring(0,30)})
        });
        const d=await r.json();
        if(d.success){
            const filename=d.file.split(/[/\\]/).pop();
            window.open('/api/download/'+filename,'_blank');
            showToast('导出成功');
        }else showToast(d.error||'导出失败','error');
    }catch(e){showToast(e.message,'error');}
}

// 测试点历史
function toggleTpHistory(){
    const c=document.getElementById('tpHistContent'),t=document.getElementById('tpHistToggle');
    if(c.style.display==='none'){c.style.display='block';t.innerHTML='收起 &#9652;';loadTpHistory();}
    else{c.style.display='none';t.innerHTML='展开 &#9662;';}
}
async function loadTpHistory(){
    const l=document.getElementById('tpHistList');
    try{
        const r=await authFetch('/api/test-points');
        const d=await r.json();
        const list=d.test_points||[];
        if(!list.length){l.innerHTML='<div style="text-align:center;color:#bbb;padding:16px;font-size:12px;">暂无历史记录</div>';return;}
        l.innerHTML=list.map(t=>`
            <div style="display:flex;align-items:center;justify-content:space-between;padding:8px 0;border-bottom:1px solid #f5f5f5;">
                <div style="flex:1;min-width:0;cursor:pointer;" onclick="loadTpRecord(${t.id})">
                    <div style="font-size:13px;color:#333;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(t.title)}</div>
                    <div style="font-size:11px;color:#999;margin-top:2px;">${t.total} 个测试点 &middot; ${esc(t.created_at)}</div>
                </div>
                <button style="font-size:11px;padding:2px 8px;border-radius:4px;border:1px solid #ddd;background:#fff;cursor:pointer;color:#999;margin-left:8px;" onclick="event.stopPropagation();deleteTpRecord(${t.id})">删除</button>
            </div>
        `).join('');
    }catch(e){l.innerHTML='<div style="text-align:center;color:#d32f2f;padding:16px;">加载失败</div>';}
}
async function loadTpRecord(id){
    try{
        const r=await authFetch('/api/test-points/'+id);
        const tp=await r.json();
        testPointsData=tp.points||[];
        renderTestPoints(testPointsData);
        showToast('已加载历史记录');
    }catch(e){showToast(e.message,'error');}
}
async function deleteTpRecord(id){
    if(!confirm('确定删除？'))return;
    try{await authFetch('/api/test-points/'+id,{method:'DELETE'});showToast('已删除');loadTpHistory();}catch(e){showToast(e.message,'error');}
}

// ============================================================
// 项目材料
// ============================================================
let matUploadedFiles = [];
const matUploadArea = document.getElementById('matUploadArea');
const matFileInput = document.getElementById('matFileInput');
const matFileList = document.getElementById('matFileList');
matUploadArea.addEventListener('click', () => matFileInput.click());
matUploadArea.addEventListener('dragover', e => { e.preventDefault(); matUploadArea.classList.add('dragover'); });
matUploadArea.addEventListener('dragleave', () => matUploadArea.classList.remove('dragover'));
matUploadArea.addEventListener('drop', e => { e.preventDefault(); matUploadArea.classList.remove('dragover'); matAddFiles(e.dataTransfer.files); });
matFileInput.addEventListener('change', () => { matAddFiles(matFileInput.files); matFileInput.value = ''; });
function matAddFiles(fs) { for (const f of fs) { if (matUploadedFiles.some(x => x.name === f.name && x.size === f.size)) continue; matUploadedFiles.push(f); } matRenderFileList(); }
function matRemoveFile(i) { matUploadedFiles.splice(i, 1); matRenderFileList(); }
function matRenderFileList() {
    if (!matUploadedFiles.length) { matFileList.innerHTML = ''; return; }
    matFileList.innerHTML = matUploadedFiles.map((f, i) => {
        const isImg = /\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name);
        const ext = f.name.split('.').pop().toUpperCase();
        const pre = isImg ? `<img src="${URL.createObjectURL(f)}">` : '';
        return `<div class="file-item">${pre}<div><div class="fn" title="${esc(f.name)}">${esc(f.name)}</div><span class="ft">${isImg ? '图片' : ext}</span></div><button class="rb" onclick="matRemoveFile(${i})">&times;</button></div>`;
    }).join('');
}
async function saveMaterial() {
    const title = document.getElementById('matTitle').value.trim();
    const content = document.getElementById('matContent').value.trim();
    if (!title) { showToast('请输入标题', 'error'); return; }
    const btn = document.getElementById('btnSaveMat'); btn.disabled = true; btn.innerHTML = '<span class="spinner"></span> 保存中...';
    try {
        const fd = new FormData(); fd.append('title', title); fd.append('content', content);
        for (const f of matUploadedFiles) fd.append('images', f);
        const r = await authFetch('/api/materials', { method: 'POST', body: fd });
        const d = await r.json();
        if (d.success) {
            showToast('保存成功');
            document.getElementById('matTitle').value = '';
            document.getElementById('matContent').value = '';
            matUploadedFiles = []; matRenderFileList();
            loadMaterials();
        } else showToast(d.error || '保存失败', 'error');
    } catch (e) { showToast(e.message, 'error'); }
    finally { btn.disabled = false; btn.innerHTML = '&#128190; 保存材料'; }
}
async function loadMaterials() {
    const list = document.getElementById('matList');
    try {
        const r = await authFetch('/api/materials');
        const d = await r.json();
        const mats = d.materials || [];
        if (!mats.length) { list.innerHTML = '<div style="text-align:center;color:#bbb;padding:30px;font-size:13px;">暂无材料，创建后可在生成时引用</div>'; return; }
        list.innerHTML = mats.map(m => `
            <div class="mat-card" id="matCard${m.id}">
                <div class="mat-card-header" onclick="toggleMatCard(${m.id})">
                    <span class="mat-arrow" id="matArrow${m.id}">&#9656;</span>
                    <span class="mat-name">${esc(m.title)}</span>
                    <span class="mat-meta">${m.image_count ? m.image_count + ' 张图' : ''}</span>
                    <button class="mat-del" onclick="event.stopPropagation();deleteMaterial(${m.id})">删除</button>
                </div>
                <div class="mat-card-body" id="matBody${m.id}"></div>
            </div>
        `).join('');
    } catch (e) { list.innerHTML = '<div style="text-align:center;color:#d32f2f;padding:20px;">加载失败</div>'; }
}
async function toggleMatCard(id) {
    const body = document.getElementById('matBody' + id);
    const arrow = document.getElementById('matArrow' + id);
    if (body.classList.contains('show')) { body.classList.remove('show'); arrow.classList.remove('open'); return; }
    try {
        const r = await authFetch('/api/materials/' + id);
        const m = await r.json();
        let html = '';
        if (m.content) html += `<div class="mat-text">${esc(m.content)}</div>`;
        if (m.images && m.images.length) {
            html += '<div class="mat-imgs">';
            m.images.forEach(img => { html += `<img src="data:${img.media_type};base64,${img.data}" alt="${esc(img.filename||'')}" onclick="window.open(this.src,'_blank')">`; });
            html += '</div>';
        }
        if (!html) html = '<div style="color:#999;font-size:12px;">无内容</div>';
        body.innerHTML = html;
        body.classList.add('show');
        arrow.classList.add('open');
    } catch (e) { showToast('加载失败', 'error'); }
}
async function deleteMaterial(id) {
    if (!confirm('确定删除这条材料？')) return;
    try { await authFetch('/api/materials/' + id, { method: 'DELETE' }); showToast('已删除'); loadMaterials(); }
    catch (e) { showToast(e.message, 'error'); }
}

// ============================================================
// 仪表盘
// ============================================================
async function loadDashboard(){
    try{
        const d=await(await authFetch('/api/dashboard')).json();
        document.getElementById('dashSessions').textContent=d.total_sessions||0;
        document.getElementById('dashTestcases').textContent=d.total_testcases||0;
        document.getElementById('dashPrefs').textContent=d.preference_count||0;
        const m=d.current_model||'-';
        document.getElementById('dashModels').textContent=m.length>12?m.substring(0,12)+'...':m;
        const list=document.getElementById('dashRecent');
        if(!d.recent||!d.recent.length){list.innerHTML='<li style="text-align:center;color:#bbb;padding:20px;font-size:13px;">暂无记录，开始生成你的第一份用例吧</li>';return;}
        list.innerHTML=d.recent.map(s=>`<li class="recent-item" onclick="loadSessionFromDash(${s.id})"><span class="ri-text">${esc(s.req_preview||s.requirement_preview||'')}</span><span class="ri-meta">${s.tc_count} 条用例 &middot; ${esc(s.created_at)}</span></li>`).join('');
    }catch(e){}
}
async function loadSessionFromDash(id){switchPage('generator');setTimeout(()=>loadSession(id),200);}

// ============================================================
// 模型配置
// ============================================================
let currentPresets={};
async function loadPresets(){
    const b=document.getElementById('presetBar');
    if(!b)return;
    b.innerHTML='<span style="color:#999;font-size:12px;">加载中...</span>';
    try{
        const r=await authFetch('/api/model-presets');
        const d=await r.json();
        currentPresets=d.presets||{};
        b.innerHTML=Object.entries(currentPresets).map(([k,v])=>`<button class="preset-btn" data-key="${k}" onclick="applyPreset('${k}')">${v.name}</button>`).join('');
    }catch(e){b.innerHTML='<span style="color:#d32f2f;font-size:12px;">加载失败</span>';}
}
function applyPreset(k){document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));document.querySelector(`.preset-btn[data-key="${k}"]`)?.classList.add('active');authFetch('/api/model-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({preset:k})}).then(r=>r.json()).then(d=>{loadModelConfig();if(d.need_key){showToast('已切换到 '+currentPresets[k].name+'，请填写 API Key','error');setTimeout(()=>{const el=document.getElementById('cfg-gen-api_key');el.focus();el.style.boxShadow='0 0 0 2px #d32f2f';setTimeout(()=>el.style.boxShadow='',2000);},300);}else{showToast('已切换到 '+currentPresets[k].name);}});}
async function loadModelConfig(){try{const r=await authFetch('/api/model-config');const d=await r.json();const c=d.config||{};const g=c.generate||{};const rv=c.review||{};document.getElementById('cfg-gen-api_type').value=g.api_type||'openai';document.getElementById('cfg-gen-model').value=g.model||'';document.getElementById('cfg-gen-base_url').value=g.base_url||'';document.getElementById('cfg-gen-api_key').value='';document.getElementById('cfg-gen-api_key').placeholder=g.api_key_hint||'留空则不修改';document.getElementById('cfg-gen-image_model').value=g.image_model||'';document.getElementById('cfg-gen-temperature').value=g.temperature??0.3;document.getElementById('cfg-gen-max_tokens').value=g.max_tokens??4096;document.getElementById('cfg-gen-max_retries').value=g.max_retries??3;document.getElementById('cfg-gen-enable_thinking').value=g.enable_thinking?'true':'false';document.getElementById('cfg-rev-enabled').value=rv.enabled?'true':'false';document.getElementById('cfg-rev-api_type').value=rv.api_type||'openai';document.getElementById('cfg-rev-base_url').value=rv.base_url||'';document.getElementById('cfg-rev-model').value=rv.model||'';document.getElementById('cfg-rev-api_key').value='';document.getElementById('cfg-rev-api_key').placeholder=rv.api_key_hint||'留空则不修改';document.getElementById('cfg-rev-temperature').value=rv.temperature??0.3;document.getElementById('cfg-rev-max_tokens').value=rv.max_tokens??4096;}catch(e){showToast('加载配置失败','error');}}
async function saveModelConfig(){const cfg={generate:{api_type:document.getElementById('cfg-gen-api_type').value,model:document.getElementById('cfg-gen-model').value,base_url:document.getElementById('cfg-gen-base_url').value,api_key:document.getElementById('cfg-gen-api_key').value,image_model:document.getElementById('cfg-gen-image_model').value||undefined,temperature:parseFloat(document.getElementById('cfg-gen-temperature').value)||0.3,max_tokens:parseInt(document.getElementById('cfg-gen-max_tokens').value)||4096,max_retries:parseInt(document.getElementById('cfg-gen-max_retries').value)||3,enable_thinking:document.getElementById('cfg-gen-enable_thinking').value==='true'},review:{enabled:document.getElementById('cfg-rev-enabled').value==='true',api_type:document.getElementById('cfg-rev-api_type').value,base_url:document.getElementById('cfg-rev-base_url').value,model:document.getElementById('cfg-rev-model').value,api_key:document.getElementById('cfg-rev-api_key').value,temperature:parseFloat(document.getElementById('cfg-rev-temperature').value)||0.3,max_tokens:parseInt(document.getElementById('cfg-rev-max_tokens').value)||4096,max_retries:3}};try{const r=await authFetch('/api/model-config',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({config:cfg})});const d=await r.json();if(d.success){showToast('配置已保存');document.querySelectorAll('.preset-btn').forEach(b=>b.classList.remove('active'));}else showToast('保存失败','error');}catch(e){showToast('保存失败','error');}}
setTimeout(loadPresets,500);

// ============================================================
// 文件上传
// ============================================================
let uploadedFiles=[];
const uploadArea=document.getElementById('uploadArea'),fileInput=document.getElementById('fileInput'),fileList=document.getElementById('fileList');
uploadArea.addEventListener('click',()=>fileInput.click());
uploadArea.addEventListener('dragover',e=>{e.preventDefault();uploadArea.classList.add('dragover');});
uploadArea.addEventListener('dragleave',()=>uploadArea.classList.remove('dragover'));
uploadArea.addEventListener('drop',e=>{e.preventDefault();uploadArea.classList.remove('dragover');addFiles(e.dataTransfer.files);});
fileInput.addEventListener('change',()=>{addFiles(fileInput.files);fileInput.value='';});
document.getElementById('requirement').addEventListener('paste',e=>{const items=e.clipboardData?.items;if(!items)return;for(const item of items){if(item.type.startsWith('image/')){e.preventDefault();const f=item.getAsFile();if(f){const ext=f.type.split('/')[1]||'png';uploadedFiles.push(new File([f],`粘贴_${Date.now()}.${ext}`,{type:f.type}));renderFileList();showToast('已添加图片');}}}});
function addFiles(fs){for(const f of fs){if(uploadedFiles.some(x=>x.name===f.name&&x.size===f.size))continue;uploadedFiles.push(f);}renderFileList();}
function removeFile(i){uploadedFiles.splice(i,1);renderFileList();}
function renderFileList(){if(!uploadedFiles.length){fileList.innerHTML='';return;}fileList.innerHTML=uploadedFiles.map((f,i)=>{const isImg=/\.(png|jpe?g|gif|webp|bmp)$/i.test(f.name);const ext=f.name.split('.').pop().toUpperCase();const pre=isImg?`<img src="${URL.createObjectURL(f)}">`:'';return`<div class="file-item">${pre}<div><div class="fn" title="${esc(f.name)}">${esc(f.name)}</div><span class="ft">${isImg?'图片':ext}</span></div><button class="rb" onclick="removeFile(${i})">&times;</button></div>`;}).join('');}
function showToast(m,t='success'){const el=document.getElementById('toast');el.textContent=m;el.className='toast '+t+' show';setTimeout(()=>el.classList.remove('show'),3000);}
function esc(s){if(!s)return '';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;').replace(/'/g,'&#39;');}

// ============================================================
// SSE
// ============================================================
function sseParseLine(line,{onProgress,onDone}){if(!line.startsWith('data: '))return;try{const ev=JSON.parse(line.slice(6));if(ev.type==='progress'&&onProgress)onProgress(ev.message);else if(ev.type==='done'&&onDone)onDone(ev.data);else if(ev.type==='error')throw new Error(ev.message);}catch(e){if(e.message&&!e.message.includes('JSON'))throw e;}}
async function sseFetch(u,b,{onProgress,onDone}){const r=await fetch(u,{method:'POST',headers:_headers({'Content-Type':'application/json'}),body:JSON.stringify(b)});if(r.status===401){doLogout();throw new Error('登录已过期');}const rd=r.body.getReader(),dec=new TextDecoder();let buf='';try{while(true){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();for(const line of lines){sseParseLine(line,{onProgress,onDone});}}}finally{rd.cancel();}if(buf.trim())sseParseLine(buf.trim(),{onProgress,onDone});}

// ============================================================
// 需求分析
// ============================================================
let analysisModules=null;
async function analyzeRequirement(){
    const req=document.getElementById('requirement').value.trim();
    if(!req&&!uploadedFiles.length){showToast('请输入需求或上传文件','error');return;}
    const btn=document.getElementById('btnAnalyze');btn.disabled=true;btn.innerHTML='<span class="spinner"></span> 分析中...';
    try{
        await sseFetch('/api/analyze',{requirement:req,case_types:document.getElementById('caseTypes').value},{
            onProgress:m=>{btn.innerHTML=`<span class="spinner"></span> ${m}`;},
            onDone:d=>{
                if(!d.modules||!d.modules.length){showToast('未能拆解模块，可直接生成','error');return;}
                analysisModules=d.modules;
                const comp=d.complexity||'medium';
                const compLabel={simple:'简单',medium:'中等',complex:'复杂'}[comp]||'中等';
                let html=`<div class="complexity-badge complexity-${comp}">复杂度: ${compLabel}</div>`;
                html+=`<div class="module-list">${d.modules.map(m=>`<div class="module-item"><span class="mi-name">${esc(m.name)}</span><span class="mi-desc">${esc(m.description||'')}</span><span class="mi-cases">~${m.estimated_cases||'?'} 条</span></div>`).join('')}</div>`;
                document.getElementById('analysisContent').innerHTML=html;
                document.getElementById('analysisCard').style.display='block';
                document.getElementById('analysisCard').scrollIntoView({behavior:'smooth'});
            }
        });
    }catch(e){showToast(e.message,'error');}
    finally{btn.disabled=false;btn.innerHTML='&#128269; 需求分析';}
}

// ============================================================
// 生成
// ============================================================
let currentFiles={},currentRequirement='',currentTestcases=[],currentReview='',originalTestcases=[],currentSessionId=null,selectedRows=new Set();
let genSelectedMats = new Set();
let isGenerating=false;  // 防止重复生成请求

async function loadGenMaterials() {
    try {
        const r = await authFetch('/api/materials');
        const d = await r.json();
        const mats = d.materials || [];
        const sel = document.getElementById('genMatSelect');
        const list = document.getElementById('genMatList');
        if (!mats.length) { sel.style.display = 'none'; return; }
        sel.style.display = 'block';
        genSelectedMats.clear();
        list.innerHTML = mats.map(m => `
            <label style="display:flex;align-items:center;gap:4px;padding:4px 8px;border:1px solid #ddd;border-radius:5px;font-size:11px;cursor:pointer;">
                <input type="checkbox" value="${m.id}" onchange="genToggleMat(${m.id})" style="accent-color:#1a73e8;">
                ${esc(m.title)}
            </label>
        `).join('');
    } catch (e) {}
}
function genToggleMat(id) { if (genSelectedMats.has(id)) genSelectedMats.delete(id); else genSelectedMats.add(id); }

// 测试点选择
let genSelectedTp = null;

async function loadGenTestPoints() {
    try {
        const r = await authFetch('/api/test-points');
        const d = await r.json();
        const tps = d.test_points || [];
        const sel = document.getElementById('genTpSelect');
        const list = document.getElementById('genTpList');
        if (!tps.length) { sel.style.display = 'none'; return; }
        sel.style.display = 'block';
        genSelectedTp = null;
        list.innerHTML = tps.map(tp => `
            <div style="display:flex;align-items:center;gap:6px;padding:4px 8px;border:1px solid #ddd;border-radius:5px;font-size:11px;cursor:pointer;transition:all 0.15s;" id="genTpItem${tp.id}" onclick="genToggleTp(${tp.id})">
                <span style="flex:1;color:#333;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(tp.title)}</span>
                <span style="color:#888;font-size:10px;">${tp.total} 点</span>
            </div>
        `).join('');
    } catch (e) {}
}

function genToggleTp(id) {
    if (genSelectedTp === id) {
        genSelectedTp = null;
        document.getElementById('genTpItem' + id).style.borderColor = '#ddd';
        document.getElementById('genTpItem' + id).style.background = '';
    } else {
        if (genSelectedTp !== null) {
            const prev = document.getElementById('genTpItem' + genSelectedTp);
            if (prev) { prev.style.borderColor = '#ddd'; prev.style.background = ''; }
        }
        genSelectedTp = id;
        document.getElementById('genTpItem' + id).style.borderColor = '#1a73e8';
        document.getElementById('genTpItem' + id).style.background = '#e8f0fe';
    }
}

async function generate(){await doGenerate(false);}
async function generateFromAnalysis(){await doGenerate(true);}
async function doGenerate(useAnalysis){
    if(isGenerating){showToast('正在生成中，请勿重复点击','error');return;}
    const req=document.getElementById('requirement').value.trim();
    if(!req&&!uploadedFiles.length){showToast('请输入需求或上传文件','error');return;}
    isGenerating=true;
    const btn=document.getElementById('btnGenerate');btn.disabled=true;btn.innerHTML='<span class="spinner"></span> 生成中...';
    document.getElementById('loading').style.display='block';document.getElementById('loadingText').textContent='AI 正在分析需求...';
    document.getElementById('result').style.display='none';document.getElementById('reviewPanel').style.display='none';document.getElementById('diffPanel').style.display='none';document.getElementById('analysisCard').style.display='none';
    try{
        const fd=new FormData();fd.append('requirement',req);fd.append('priority',document.getElementById('priority').value);fd.append('case_types',document.getElementById('caseTypes').value);
        if(genSelectedMats.size>0)fd.append('material_ids',[...genSelectedMats].join(','));
        if(genSelectedTp!==null)fd.append('test_point_id',genSelectedTp);
        for(const f of uploadedFiles)fd.append('files',f);
        const resp=await fetch('/api/generate',{method:'POST',body:fd});if(resp.status===401){doLogout();throw new Error('登录已过期');}
        const rd=resp.body.getReader(),dec=new TextDecoder();let buf='';
        while(true){const{done,value}=await rd.read();if(done)break;buf+=dec.decode(value,{stream:true});const lines=buf.split('\n');buf=lines.pop();for(const line of lines){if(!line.startsWith('data: '))continue;try{const ev=JSON.parse(line.slice(6));if(ev.type==='progress')document.getElementById('loadingText').textContent=ev.message;else if(ev.type==='done'){const d=ev.data;currentTestcases=d.testcases;currentFiles=d.files;currentRequirement=req;currentSessionId=d.session_id||null;originalTestcases=[];renderResult(d);showToast(`生成成功！共 ${d.count} 条用例`);}else if(ev.type==='error')throw new Error(ev.message);}catch(e){if(e.message&&!e.message.includes('JSON'))throw e;}}}
    }catch(e){showToast(e.message,'error');}finally{isGenerating=false;btn.disabled=false;btn.innerHTML='&#9889; 直接生成';document.getElementById('loading').style.display='none';}
}
function renderResult(data){
    const tc=data.testcases,c={P0:0,P1:0,P2:0,P3:0};tc.forEach(t=>{c[t.priority]=(c[t.priority]||0)+1;});
    document.getElementById('totalCount').textContent=tc.length;
    document.getElementById('p0Count').textContent=c.P0;document.getElementById('p1Count').textContent=c.P1;
    document.getElementById('p2Count').textContent=c.P2;document.getElementById('p3Count').textContent=c.P3;
    const info=data.input;let infoText='';if(info){const p=[];if(info.has_text)p.push('文字');if(info.image_count>0)p.push(info.image_count+'张图');if(p.length)infoText='输入: '+p.join('+');}
    document.getElementById('inputInfo').textContent=infoText;
    selectedRows.clear();updateBatchBar();
    document.getElementById('tbody').innerHTML=tc.map((t,i)=>`
        <tr id="row-${i}"><td class="cb-cell"><input type="checkbox" class="row-cb" data-idx="${i}" onchange="toggleRowSelect(${i})"></td>
        <td><strong>${esc(t.id)}</strong></td><td>${esc(t.module)}</td><td>${esc(t.title)}</td>
        <td><span class="priority priority-${esc(t.priority)}">${esc(t.priority)}</span></td>
        <td><span class="type-tag">${esc(t.type)}</span></td>
        <td><span class="src-tag" title="${esc(t.source||'')}">${esc(t.source||'-')}</span></td>
        <td><button class="action-btn" onclick="toggleDetail(${i})">详情</button> <button class="action-btn" onclick="editCase(${i})">编辑</button></td></tr>
        <tr class="detail-row" id="detail-${i}"><td colspan="8"><div class="detail-grid">
        <div><dt>前置条件</dt><dd>${esc(t.precondition)||'无'}</dd></div>
        <div><dt>预期结果</dt><dd>${esc(t.expected)}</dd></div>
        <div style="grid-column:1/-1"><dt>测试步骤</dt><dd>${esc(t.steps).replace(/\\n/g,'<br>')}</dd></div>
        </div></td></tr>
    `).join('');
    document.getElementById('result').style.display='block';
    document.getElementById('batchBar').style.display='flex';
}
function toggleDetail(i){document.getElementById('detail-'+i).classList.toggle('show');}
function downloadFile(type){
    if(type==='json'){
        const blob=new Blob([JSON.stringify(currentTestcases,null,2)],{type:'application/json'});
        const url=URL.createObjectURL(blob);window.open(url,'_blank');URL.revokeObjectURL(url);return;
    }
    const p=type==='excel'?currentFiles.excel:currentFiles.markdown;if(!p)return;
    const filename=p.split(/[/\\]/).pop();
    const qs=currentSessionId?'?session_id='+currentSessionId:'';
    window.open('/api/download/'+filename+qs,'_blank');
}

// ============================================================
// 批量操作
// ============================================================
function toggleRowSelect(i){if(selectedRows.has(i))selectedRows.delete(i);else selectedRows.add(i);updateBatchBar();}
function toggleSelectAll(){const cb=document.getElementById('selectAll').checked;selectedRows.clear();if(cb)currentTestcases.forEach((_,i)=>selectedRows.add(i));document.querySelectorAll('.row-cb').forEach(c=>c.checked=cb);updateBatchBar();}
function updateBatchBar(){document.getElementById('selectedCount').textContent=selectedRows.size+' 条已选';}
function batchChangePriority(){
    const val=document.getElementById('batchPriority').value;if(!val||!selectedRows.size){showToast('请先选择用例和优先级','error');return;}
    selectedRows.forEach(i=>{currentTestcases[i].priority=val;});
    renderResult({testcases:currentTestcases,count:currentTestcases.length,files:currentFiles,session_id:currentSessionId,input:{}});
    showToast(`已将 ${selectedRows.size} 条用例改为 ${val}`);
}
function batchDelete(){
    if(!selectedRows.size){showToast('请先选择用例','error');return;}
    if(!confirm(`确定删除 ${selectedRows.size} 条用例？`))return;
    const sorted=[...selectedRows].sort((a,b)=>b-a);sorted.forEach(i=>currentTestcases.splice(i,1));
    currentTestcases.forEach((t,j)=>{t.id='TC_'+String(j+1).padStart(3,'0');});
    renderResult({testcases:currentTestcases,count:currentTestcases.length,files:currentFiles,session_id:currentSessionId,input:{}});
    showToast(`已删除 ${sorted.length} 条用例`);
}

// ============================================================
// 评审 / 优化
// ============================================================
async function review(){const b=document.getElementById('btnReview');b.disabled=true;b.innerHTML='<span class="spinner"></span> 评审中...';try{await sseFetch('/api/review',{requirement:currentRequirement,testcases:currentTestcases},{onProgress:m=>{b.innerHTML=`<span class="spinner"></span> ${m}`;},onDone:d=>{currentReview=d.review;document.getElementById('reviewContent').textContent=d.review;document.getElementById('reviewPanel').style.display='block';document.getElementById('reviewPanel').scrollIntoView({behavior:'smooth'});showToast('评审完成');}});}catch(e){showToast(e.message,'error');}finally{b.disabled=false;b.innerHTML='&#128269; AI 评审';}}
async function optimize(){const b=document.getElementById('btnOptimize');b.disabled=true;b.innerHTML='<span class="spinner"></span> 优化中...';originalTestcases=JSON.parse(JSON.stringify(currentTestcases));try{await sseFetch('/api/optimize',{requirement:currentRequirement,testcases:currentTestcases,review:currentReview},{onProgress:m=>{b.innerHTML=`<span class="spinner"></span> ${m}`;},onDone:d=>{currentTestcases=d.testcases;currentFiles=d.files;renderResult(d);showDiff(originalTestcases,d.testcases);showToast(`优化完成！共 ${d.count} 条用例`);}});}catch(e){showToast(e.message,'error');}finally{b.disabled=false;b.innerHTML='&#128640; 根据评审优化用例';}}
function showDiff(oc,nc){
    const TH=0.4;function sim(a,b){if(!a||!b)return 0;const n=s=>s.replace(/\s+/g,'').toLowerCase(),na=n(a),nb=n(b);if(na===nb)return 1;const[sh,lo]=na.length<=nb.length?[na,nb]:[nb,na];let m=0;for(const c of sh)if(lo.includes(c))m++;return sh.length?m/sh.length:0;}
    function tcSim(a,b){return sim(a.title,b.title)*.4+sim(a.steps,b.steps)*.3+sim(a.expected,b.expected)*.3;}
    const add=[],mod=[],rem=[],mo=new Set(),mn=new Set(),pairs=[];
    nc.forEach((n,ni)=>{oc.forEach((o,oi)=>{const s=tcSim(n,o);if(s>=TH)pairs.push({ni,oi,s,n,o});});});
    pairs.sort((a,b)=>b.s-a.s);pairs.forEach(p=>{if(mn.has(p.ni)||mo.has(p.oi))return;mn.add(p.ni);mo.add(p.oi);if(p.s<0.95){const ch=[];if(p.o.title!==p.n.title)ch.push('标题变更');if(p.o.steps!==p.n.steps)ch.push('步骤更新');if(p.o.expected!==p.n.expected)ch.push('预期更新');if(p.o.priority!==p.n.priority)ch.push(`${p.o.priority}→${p.n.priority}`);if(ch.length)mod.push({tc:p.n,changes:ch});}});
    nc.forEach((t,i)=>{if(!mn.has(i))add.push(t);});oc.forEach((t,i)=>{if(!mo.has(i))rem.push(t);});
    if(!add.length&&!mod.length&&!rem.length){document.getElementById('diffPanel').style.display='none';return;}
    document.getElementById('diffStats').innerHTML=`<div class="diff-stat add"><div class="num">+${add.length}</div><div class="lbl">新增</div></div><div class="diff-stat mod"><div class="num">${mod.length}</div><div class="lbl">修改</div></div><div class="diff-stat del"><div class="num">-${rem.length}</div><div class="lbl">删除</div></div>`;
    let h='';add.forEach(t=>{h+=`<div class="diff-item diff-added"><span class="dl">新增</span> ${esc(t.title)}</div>`;});mod.forEach(({tc,c})=>{h+=`<div class="diff-item diff-modified"><span class="dl">修改</span> ${esc(tc.title)} — ${esc(c.join(', '))}</div>`;});rem.forEach(t=>{h+=`<div class="diff-item diff-removed"><span class="dl">删除</span> ${esc(t.title)}</div>`;});
    document.getElementById('diffContent').innerHTML=h;document.getElementById('diffPanel').style.display='block';document.getElementById('diffPanel').scrollIntoView({behavior:'smooth'});
}

// ============================================================
// 历史记录
// ============================================================
function toggleHistory(){const s=document.getElementById('historySidebar'),o=document.getElementById('sidebarOverlay');if(s.classList.contains('open')){s.classList.remove('open');o.classList.remove('show');}else{s.classList.add('open');o.classList.add('show');loadHistory();}}
async function loadHistory(){const l=document.getElementById('historyList');try{const r=await authFetch('/api/history?limit=50');const d=await r.json();const ss=d.sessions||[];if(!ss.length){l.innerHTML='<div class="history-empty">暂无历史记录</div>';return;}l.innerHTML=ss.map(s=>`<div class="history-item"><div class="h-time">${esc(s.created_at)}</div><div class="h-preview" title="${esc(s.requirement)}">${esc(s.requirement_preview)}</div><div class="h-meta"><span>${s.tc_count} 条</span>${s.priority?'<span>'+esc(s.priority)+'</span>':''}</div><div class="h-actions"><button onclick="loadSession(${s.id})">加载</button><button class="del-btn" onclick="deleteSession(${s.id},event)">删除</button></div></div>`).join('');}catch(e){l.innerHTML='<div class="history-empty">加载失败</div>';}}
async function loadSession(id){try{const r=await authFetch('/api/history/'+id);if(!r.ok)throw new Error('记录不存在');const s=await r.json();document.getElementById('requirement').value=s.requirement||'';if(s.priority)document.getElementById('priority').value=s.priority;if(s.case_types)document.getElementById('caseTypes').value=s.case_types.join(',');currentTestcases=s.testcases;currentRequirement=s.requirement;currentSessionId=s.id;currentReview=s.review_report||'';currentFiles={};originalTestcases=[];renderResult({testcases:s.testcases,count:s.testcases.length,files:{},session_id:s.id,input:{has_text:!!s.requirement,image_count:s.images?s.images.length:0,image_names:s.images?s.images.map(i=>i.filename||''):[]}});if(currentReview){document.getElementById('reviewContent').textContent=currentReview;document.getElementById('reviewPanel').style.display='block';}toggleHistory();showToast('已加载 #'+id);document.getElementById('result').scrollIntoView({behavior:'smooth'});}catch(e){showToast(e.message,'error');}}
async function deleteSession(id,ev){ev.stopPropagation();if(!confirm('确定删除？'))return;try{await authFetch('/api/history/'+id,{method:'DELETE'});showToast('已删除');loadHistory();}catch(e){showToast(e.message,'error');}}

// ============================================================
// 内联编辑
// ============================================================
function editCase(i){const tc=currentTestcases[i],row=document.getElementById('row-'+i),dr=document.getElementById('detail-'+i);row.innerHTML=`<td class="cb-cell"><input type="checkbox" class="row-cb" data-idx="${i}"></td><td><strong>${esc(tc.id)}</strong></td><td><input class="edit-input" id="edit-module-${i}" value="${esc(tc.module)}" style="width:75px;"></td><td><input class="edit-input" id="edit-title-${i}" value="${esc(tc.title)}"></td><td><select id="edit-priority-${i}" style="padding:2px;border:1px solid #1a73e8;border-radius:4px;font-size:11px;"><option value="P0" ${tc.priority==='P0'?'selected':''}>P0</option><option value="P1" ${tc.priority==='P1'?'selected':''}>P1</option><option value="P2" ${tc.priority==='P2'?'selected':''}>P2</option><option value="P3" ${tc.priority==='P3'?'selected':''}>P3</option></select></td><td><input class="edit-input" id="edit-type-${i}" value="${esc(tc.type)}" style="width:65px;font-size:11px;"></td><td><span class="src-tag">${esc(tc.source||'-')}</span></td><td class="edit-actions"><button class="edit-save" onclick="saveEdit(${i})">保存</button><button class="edit-cancel" onclick="cancelEdit(${i})">取消</button></td>`;dr.classList.add('show');dr.innerHTML=`<td colspan="8" style="background:#f8fbff;"><div class="detail-grid"><div><dt>前置条件</dt><textarea class="edit-textarea" id="edit-precondition-${i}" rows="2">${esc(tc.precondition)}</textarea></div><div><dt>预期结果</dt><textarea class="edit-textarea" id="edit-expected-${i}" rows="2">${esc(tc.expected)}</textarea></div><div style="grid-column:1/-1"><dt>测试步骤</dt><textarea class="edit-textarea" id="edit-steps-${i}" rows="3">${esc(tc.steps)}</textarea></div></div></td>`;}
function saveEdit(i){const tc=currentTestcases[i];if(!originalTestcases.length)originalTestcases=JSON.parse(JSON.stringify(currentTestcases));tc.module=document.getElementById('edit-module-'+i).value;tc.title=document.getElementById('edit-title-'+i).value;tc.priority=document.getElementById('edit-priority-'+i).value;tc.type=document.getElementById('edit-type-'+i).value;tc.precondition=document.getElementById('edit-precondition-'+i).value;tc.expected=document.getElementById('edit-expected-'+i).value;tc.steps=document.getElementById('edit-steps-'+i).value;renderResult({testcases:currentTestcases,count:currentTestcases.length,files:currentFiles,session_id:currentSessionId,input:{}});document.getElementById('btnSavePref').style.display='';showToast('已保存');}
function cancelEdit(i){renderResult({testcases:currentTestcases,count:currentTestcases.length,files:currentFiles,session_id:currentSessionId,input:{}});}

// ============================================================
// 偏好
// ============================================================
async function savePreferences(){if(!originalTestcases.length){showToast('没有修改','error');return;}const b=document.getElementById('btnSavePref');b.disabled=true;b.innerHTML='<span class="spinner"></span> 分析中...';try{await sseFetch('/api/preferences/extract',{original:originalTestcases,edited:currentTestcases,session_id:currentSessionId},{onProgress:m=>{b.innerHTML=`<span class="spinner"></span> ${m}`;},onDone:d=>{if(d.preferences?.length){showToast(`提取 ${d.count} 条偏好`);loadPreferences();}else showToast('未提取到偏好');originalTestcases=[];b.style.display='none';}});}catch(e){showToast(e.message,'error');}finally{b.disabled=false;b.innerHTML='&#128161; 保存偏好';}}
function togglePrefPanel(){const c=document.getElementById('prefContent'),t=document.getElementById('prefToggle');if(c.style.display==='none'){c.style.display='block';t.innerHTML='收起 &#9652;';loadPreferences();}else{c.style.display='none';t.innerHTML='展开 &#9662;';}}
async function loadPreferences(){const l=document.getElementById('prefList');try{const r=await authFetch('/api/preferences');const d=await r.json();const ps=d.preferences||[];if(!ps.length){l.innerHTML='<div style="text-align:center;color:#bbb;padding:16px;font-size:12px;">暂无偏好规则</div>';return;}l.innerHTML=ps.map(p=>`<div class="pref-item ${p.active?'':'inactive'}"><span class="pref-cat">${esc(p.category)}</span><span class="pref-pattern">${esc(p.pattern)}</span><span class="pref-weight">权重 ${p.weight.toFixed(1)}</span><button onclick="togglePref(${p.id},${p.active?0:1})">${p.active?'禁用':'启用'}</button><button onclick="deletePref(${p.id})" style="color:#d32f2f;">删除</button></div>`).join('');}catch(e){l.innerHTML='<div style="text-align:center;color:#d32f2f;padding:16px;">加载失败</div>';}}
async function togglePref(id,a){try{await authFetch('/api/preferences/'+id,{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({active:a})});showToast(a?'已启用':'已禁用');loadPreferences();}catch(e){showToast(e.message,'error');}}
async function deletePref(id){if(!confirm('确定删除？'))return;try{await authFetch('/api/preferences/'+id,{method:'DELETE'});showToast('已删除');loadPreferences();}catch(e){showToast(e.message,'error');}}
