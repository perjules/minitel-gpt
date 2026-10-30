"""Real forms and persistence, with synthetic keys and no provider calls."""

import re
import shutil
import subprocess

import pytest

from services import admin_ui as admin
from services import config_store, gmail_mail


def form(**updates):
    return {
        "llm_provider": "mistral",
        "mistral_model": "mistral-small-latest",
        "gmail_overrides": "1",
        "gmail_model": "",
        "gmail_draft_model": "mistral-large-4-0",
        "gmail_draft_reasoning_effort": "medium",
        **updates,
    }


def test_profile_form_persists_applies_and_can_clear_overrides(console):
    client, headers, _ = console
    config_store.update_env(admin.ENV_FILE, {"MISTRAL_KEY": "PRIVATE-KEY"})
    assert client.post("/save-llm", data=form()).status_code == 403
    assert client.post("/save-llm", data=form(), headers=headers).status_code == 302
    saved = admin.read_env()
    assert saved["MISTRAL_GMAIL_DRAFT_MODEL"] == "mistral-large-4-0"
    assert saved["MISTRAL_GMAIL_DRAFT_REASONING_EFFORT"] == "medium"
    assert saved["MISTRAL_KEY"] == "PRIVATE-KEY"
    admin.restart_terminal.assert_called_once()
    page = client.get("/?section=ai").text
    assert "Gmail — modèles par tâche" in page and "PRIVATE-KEY" not in page
    assert (
        'value="medium" selected' in page
        and 'value="mistral-large-4-0" selected' in page
    )
    # The administration adapter observes the same saved overrides as the terminal.
    model = gmail_mail.MailModel(env=lambda: {**admin.os.environ, **admin.read_env()})
    assert model.profile(True)["model"] == "mistral-large-4-0"
    response = client.post(
        "/save-llm",
        headers=headers,
        data=form(gmail_draft_model="", gmail_draft_reasoning_effort=""),
    )
    assert response.status_code == 302
    assert (
        model.profile(True)["model"]
        == model.profile()["model"]
        == "mistral-small-latest"
    )


def test_invalid_combination_rejects_entire_save_and_inherited_model_change(console):
    client, headers, _ = console
    config_store.update_env(
        admin.ENV_FILE,
        {"MISTRAL_KEY": "old", "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "high"},
    )
    before = admin.ENV_FILE.read_bytes()
    for values in [
        form(mistral_key="new", gmail_draft_reasoning_effort="none"),
        form(gmail_draft_model="bad\nMODEL=other"),
        {"mistral_model": "unknown-model"},
    ]:
        assert client.post("/save-llm", data=values, headers=headers).status_code == 400
        assert admin.ENV_FILE.read_bytes() == before
    admin.restart_terminal.assert_not_called()


def test_invalid_manual_configuration_keeps_console_usable(console):
    client, _, _ = console
    config_store.update_env(
        admin.ENV_FILE,
        {
            "MISTRAL_GMAIL_DRAFT_MODEL": "mistral-large-4-0",
            "MISTRAL_GMAIL_DRAFT_REASONING_EFFORT": "none",
        },
    )
    page = client.get("/?section=ai")
    assert page.status_code == 200
    assert (
        "Réglages Gmail incompatibles" in page.text
        and "none (incompatible)" in page.text
    )


def test_manual_model_and_effort_choices_are_shown_with_inheritance(console):
    client, headers, _ = console
    response = client.post(
        "/save-llm",
        headers=headers,
        data=form(
            gmail_model_custom="mail-model",
            gmail_draft_model_custom="mistral-medium-3-5",
            gmail_draft_reasoning_effort="high",
        ),
    )
    assert response.status_code == 302
    context = admin.gmail_model_settings()
    assert context["general"]["model"] == "mail-model"
    assert context["draft"]["model"] == "mistral-medium-3-5"
    assert context["efforts"] == ("none", "high")
    assert "key" not in context["draft"] and "key" not in context["general"]


def test_rendered_model_controls_keep_choices_on_catalogue_failure_and_validate_effort(
    console,
):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is installed in CI for rendered JavaScript checks")
    client, _, _ = console
    html = client.get("/?section=ai").text
    script = re.search(
        r"const modelLists=new Set\(\);.*?(?=// Les brouillons)", html, re.DOTALL
    )[0]
    # Minimal DOM primitives; execute the actual rendered application functions.
    harness = r"""
const assert=require('node:assert/strict');
class Option { constructor(text,value){this.text=text;this.value=value;} }
class Select {
 constructor(value,inherit=false){this.dataset={inherit:inherit?'1':''};this.options=[new Option(value,value)];this.current=value;}
 get value(){return this.current;}
 set value(value){this.current=this.options.some(o=>o.value===value)?value:'';}
 replaceChildren(...options){this.options=options;this.current=options[0]?.value||'';}
 add(option){this.options.push(option);}
 addEventListener(){}
 setCustomValidity(message){this.validationMessage=message;}
}
const fields={mistral_model:new Select('mistral-small-latest'), gmail_model:new Select('',true),
 gmail_draft_model:new Select('',true), gmail_draft_reasoning_effort:new Select(''),
 mistral_key:{value:'synthetic'}, mistral_key_clear:{checked:false}};
for(const name of ['mistral_model','gmail_model','gmail_draft_model'])fields[name+'_custom']={value:'',addEventListener(){}};
const nodes={llmForm:{elements:fields}, providerSel:{value:'mistral'}, ai:{classList:{contains:()=>false}},
 gmailModelSettings:{addEventListener(){}}, gmailModelPreview:{}, mistralModelStatus:{}, gmailCatalogueStatus:{}};
const document={getElementById:id=>nodes[id],querySelectorAll:selector=>selector==='.provider-settings'?[]:
 [fields.mistral_model,fields.gmail_model,fields.gmail_draft_model]};
let fetch=async()=>({json:async()=>({ok:true,models:[
 {id:'mistral-small-latest',label:'Small'}, {id:'mistral-large-4-0',label:'Large'},
 {id:'mistral-medium-3-5',label:'Medium'}, {id:'mistral-medium-2604',label:'Medium'}]})});
const CSRF_TOKEN='synthetic';
"""
    checks = r"""
(async()=>{
 await loadModels('mistral',true);
 assert.equal(fields.gmail_draft_model.value,'');
 assert(fields.gmail_model.options.some(o=>o.text==='Medium · mistral-medium-2604'));
 fields.gmail_draft_model.value='mistral-large-4-0'; refreshGmailProfiles();
 assert.deepEqual(fields.gmail_draft_reasoning_effort.options.map(o=>o.value),['','low','medium','high']);
 fields.gmail_draft_reasoning_effort.value='medium'; refreshGmailProfiles();
 assert.equal(fields.gmail_draft_reasoning_effort.validationMessage,'');
 assert(nodes.gmailModelPreview.textContent.includes('rédaction mistral-large-4-0'));
 fetch=async()=>({json:async()=>({ok:false,error:'Unavailable'})});
 await loadModels('mistral',true);
 assert.equal(fields.gmail_draft_model.value,'mistral-large-4-0');
 assert.equal(fields.gmail_draft_reasoning_effort.value,'medium');
 assert.equal(nodes.mistralModelStatus.textContent,'Unavailable');
 assert.equal(nodes.gmailCatalogueStatus.textContent,'Unavailable');
 fields.gmail_draft_model.value='mistral-small-latest'; refreshGmailProfiles();
 assert(fields.gmail_draft_reasoning_effort.validationMessage);
 fields.gmail_draft_reasoning_effort.value='high'; refreshGmailProfiles();
 assert.equal(fields.gmail_draft_reasoning_effort.validationMessage,'');
 fields.gmail_draft_model.value=''; fields.gmail_draft_reasoning_effort.value=''; refreshGmailProfiles();
 assert(nodes.gmailModelPreview.textContent.includes('rédaction mistral-small-latest'));
})().catch(error=>{console.error(error);process.exitCode=1;});
"""
    result = subprocess.run(
        [node],
        check=False,
        input=harness + script + checks,
        text=True,
        capture_output=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
