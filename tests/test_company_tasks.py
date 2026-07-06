import json
from concurrent.futures import Future
from types import SimpleNamespace

import pytest
from django.contrib.auth import get_user_model

from apps.companies import tasks
from apps.companies.dna_schemas import PRODUCT_LAYER_KEYS
from apps.companies.llm_client import MockLLMClient
from apps.companies.models import (
    Company,
    CompanyDNA,
    ConsistencyIssue,
    LLMCall,
    PipelineRun,
    Product,
    ProductDNA,
    ProductFile,
    Source,
)


def _specialist_content(prefix="contenuto"):
    return {key: f"{prefix} {key}" for key in PRODUCT_LAYER_KEYS}


def _make_company(schema="taskco"):
    company = Company.objects.create(
        schema_name=schema,
        name="Task Co",
        settore_primario=Company.ARCHETIPO_INSTALLAZIONE,
        prodotto_fisico=True,
        cliente_diretto=Company.CLIENTE_B2B_TECNICO,
        custom_frequenza=Company.CUSTOM_RARAMENTE,
        installatori_in_filiera=True,
        contesto_libero="Cliente tecnico con posa in cantiere e custom su lotto.",
    )
    CompanyDNA.objects.create(
        company=company,
        version=1,
        dna_type=CompanyDNA.TYPE_COMPLETE,
        content={
            "identita": "Azienda tecnica specializzata in acciaio INOX.",
            "nucleo_tecnico": "Canali ispezionabili e drenaggio tecnico.",
        },
        is_current=True,
    )
    return company


def _make_product(company, slug="canale", codice="CI-001"):
    product = Product.objects.create(
        company=company,
        name="Canale Ispezionabile",
        slug=slug,
        codice=codice,
        status=Product.STATUS_IN_COSTRUZIONE,
    )
    ProductFile.objects.create(
        product=product,
        original_name="scheda-tecnica.txt",
        content_text=(
            "Canale in acciaio INOX AISI 304, spessore 2mm, sezione 90x90mm, "
            "portata 12 l/s, temperatura massima 80C."
        ),
        file_size=128,
    )
    return product


def _make_llm_call(company, prompt="prompt"):
    return LLMCall.objects.create(
        company=company,
        model_name="mock",
        prompt_text=prompt,
        response_text="{}",
        tokens_in=1,
        tokens_out=1,
        cost_usd=0,
        latency_ms=1,
    )


@pytest.mark.django_db
class TestProductDNATaskPipeline:
    def test_extract_concept_map_uses_documents_and_logs_call(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        monkeypatch.setattr(tasks, "get_llm_client", lambda: MockLLMClient())

        concept_map = tasks._extract_concept_map(product, company)

        assert concept_map["parameters"][0]["name"] == "spessore"
        call = LLMCall.objects.order_by("id").last()
        assert "CONCEPT_MAP_SPECIALISTA" in call.prompt_text
        assert "scheda-tecnica.txt" in call.prompt_text
        assert "Canale Ispezionabile" in call.prompt_text

    def test_seed_variants_cover_concept_map_and_document_fallback(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        concept_map = {"entities": [], "relations": [], "parameters": [], "gaps": []}
        monkeypatch.setattr(tasks, "get_llm_client", lambda: MockLLMClient())

        material_seed = tasks._generate_seed_variant(concept_map, company, product, "materials")
        workflow_seed = tasks._generate_seed_variant(None, company, product, "workflow")

        prompts = list(LLMCall.objects.order_by("id").values_list("prompt_text", flat=True))
        assert "CONCEPT MAP" in prompts[-2]
        assert "DOCUMENTI" in prompts[-1]
        assert "AISI 304" in material_seed["architettura"]
        assert "Manutenzione" in workflow_seed["applicazione"]

    def test_merge_and_single_section_refinement_use_mock_llm(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        concept_map = {"entities": [], "relations": [], "parameters": [], "gaps": []}
        monkeypatch.setattr(tasks, "get_llm_client", lambda: MockLLMClient())

        variants = {
            angle: tasks._generate_seed_variant(concept_map, company, product, angle)
            for angle in tasks._SEED_ANGLES
        }
        merged, llm_call = tasks._merge_seed_variants(variants, concept_map, company, product)
        refined = tasks._refine_single_section(
            "specifiche", merged["specifiche"], concept_map, product, company,
        )

        assert "MERGE_DNA_SPECIALISTA" in llm_call.prompt_text
        assert "configurazione" in merged
        assert "90x90mm" in refined
        assert "EN 1253-2" in refined

    def test_parallel_refinement_keeps_original_section_when_one_refine_fails(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        merged = _specialist_content("originale")

        class ImmediateExecutor:
            def __init__(self, max_workers):
                self.max_workers = max_workers

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def submit(self, fn, *args):
                future = Future()
                try:
                    future.set_result(fn(*args))
                except Exception as exc:
                    future.set_exception(exc)
                return future

        def fake_refine(key, current_text, concept_map, product_arg, company_arg):
            if key == "vincoli":
                raise RuntimeError("boom")
            return f"raffinato {key}"

        monkeypatch.setattr(tasks, "_refine_single_section", fake_refine)
        monkeypatch.setattr(tasks, "ThreadPoolExecutor", ImmediateExecutor)

        refined = tasks._refine_sections_parallel(merged, {}, product, company)

        assert refined["specifiche"] == "raffinato specifiche"
        assert refined["vincoli"] == "originale vincoli"

    def test_generate_product_dna_orchestrates_seed_merge_refine_and_save(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        seen_angles = []

        monkeypatch.setattr(
            tasks,
            "_extract_concept_map",
            lambda product_arg, company_arg: {"parameters": []},
        )

        def fake_seed(concept_map, company_arg, product_arg, angle):
            seen_angles.append(angle)
            return _specialist_content(angle)

        def fake_merge(variants, concept_map, company_arg, product_arg):
            return _specialist_content("merged"), _make_llm_call(company_arg, "merge")

        def fake_refine(content, concept_map, product_arg, company_arg, tenant_schema=None):
            return _specialist_content("refined")

        monkeypatch.setattr(tasks, "_generate_seed_variant", fake_seed)
        monkeypatch.setattr(tasks, "_merge_seed_variants", fake_merge)
        monkeypatch.setattr(tasks, "_refine_sections_parallel", fake_refine)

        dna, llm_call = tasks._generate_product_dna(product, company)

        assert sorted(seen_angles) == sorted(tasks._SEED_ANGLES)
        assert dna.version == 1
        assert dna.dna_type == ProductDNA.TYPE_PRE
        assert dna.content["specifiche"] == "refined specifiche"
        assert llm_call.prompt_text == "merge"

    def test_generate_product_dna_falls_back_when_all_seed_variants_fail(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        fallback = object()

        monkeypatch.setattr(tasks, "_extract_concept_map", lambda *args: {"parameters": []})

        def fail_seed(*args, **kwargs):
            raise RuntimeError("seed failed")

        monkeypatch.setattr(tasks, "_generate_seed_variant", fail_seed)
        monkeypatch.setattr(tasks, "_generate_product_dna_singlepass", lambda *args: fallback)

        assert tasks._generate_product_dna(product, company) is fallback

    def test_singlepass_product_dna_uses_concept_map_and_saves_pre_dna(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        concept_map = {"parameters": [{"name": "portata", "value": "12", "unit": "l/s"}]}
        monkeypatch.setattr(tasks, "get_llm_client", lambda: MockLLMClient())

        dna, llm_call = tasks._generate_product_dna_singlepass(product, company, concept_map)

        assert dna.content["specifiche"]
        assert dna.version == 1
        assert "CONCEPT MAP" in llm_call.prompt_text
        assert ProductDNA.objects.filter(product=product, is_current=True).count() == 1


@pytest.mark.django_db
class TestAsyncCompanyTasks:
    def test_run_pipeline_marks_completed_when_source_is_already_scraped(self, monkeypatch):
        company = _make_company()
        source = Source.objects.create(
            company=company,
            url="https://task.example",
            status=Source.STATUS_SCRAPED,
            scraped_data={"markdown": "Sito gia letto"},
        )
        run = PipelineRun.objects.create(company=company, source=source)

        def fake_generate(source_arg, company_arg):
            company_arg.dna_versions.filter(is_current=True).update(is_current=False)
            dna = CompanyDNA.objects.create(
                company=company_arg,
                version=2,
                dna_type=CompanyDNA.TYPE_PRE,
                content={"identita": "pre"},
            )
            return dna, _make_llm_call(company_arg, "pre-dna")

        monkeypatch.setattr(tasks, "_generate_dna", fake_generate)

        tasks.run_pipeline(run.id)

        run.refresh_from_db()
        assert run.status == PipelineRun.STATUS_COMPLETED
        assert run.current_step == "done"
        assert run.completed_at is not None

    def test_generate_complete_dna_task_calls_view_helper(self, monkeypatch):
        user = get_user_model().objects.create_user("u", "u@example.com", "pw")
        company = _make_company()
        pre_dna = CompanyDNA.objects.create(
            company=company,
            version=2,
            dna_type=CompanyDNA.TYPE_PRE,
            content={"identita": "pre"},
            is_current=False,
        )
        called = {}

        def fake_create(company_arg, pre_dna_arg, user_arg):
            called["company"] = company_arg
            called["pre_dna"] = pre_dna_arg
            called["user"] = user_arg

        monkeypatch.setattr("apps.companies.views._create_complete_dna", fake_create)

        tasks.generate_complete_dna(company.id, pre_dna.id, user.id)

        assert called == {"company": company, "pre_dna": pre_dna, "user": user}

    def test_generate_complete_product_dna_task_calls_view_helper(self, monkeypatch):
        user = get_user_model().objects.create_user("p", "p@example.com", "pw")
        company = _make_company()
        product = _make_product(company)
        pre_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content=_specialist_content("pre"),
        )
        called = {}

        def fake_create(product_arg, pre_dna_arg, user_arg):
            called["product"] = product_arg
            called["pre_dna"] = pre_dna_arg
            called["user"] = user_arg

        monkeypatch.setattr("apps.companies.views._create_complete_product_dna", fake_create)

        tasks.generate_complete_product_dna(product.id, pre_dna.id, user.id)
        assert called == {"product": product, "pre_dna": pre_dna, "user": user}

    def test_generate_product_questions_task_calls_question_generator(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        pre_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content=_specialist_content("pre"),
        )
        called = {}

        def fake_questions(product_arg, pre_dna_arg):
            called["product"] = product_arg
            called["pre_dna"] = pre_dna_arg

        monkeypatch.setattr("apps.companies.views._generate_product_questions", fake_questions)

        tasks.generate_product_questions_task(product.id, pre_dna.id)
        assert called == {"product": product, "pre_dna": pre_dna}

    def test_generate_product_dna_task_dispatches_question_generation(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        dispatched = {}

        def fake_generate(product_arg, company_arg, tenant_schema=None):
            dna = ProductDNA.objects.create(
                product=product_arg,
                version=1,
                dna_type=ProductDNA.TYPE_PRE,
                content=_specialist_content("pre"),
            )
            return dna, _make_llm_call(company_arg, "product-pre")

        def fake_delay(product_id, dna_id, tenant_schema=None):
            dispatched["product_id"] = product_id
            dispatched["dna_id"] = dna_id
            dispatched["tenant_schema"] = tenant_schema

        monkeypatch.setattr(tasks, "_generate_product_dna", fake_generate)
        monkeypatch.setattr(tasks.generate_product_questions_task, "delay", fake_delay)

        tasks.generate_product_dna_task(product.id, tenant_schema="tenant1")

        dna = ProductDNA.objects.get(product=product)
        assert dispatched == {
            "product_id": product.id,
            "dna_id": dna.id,
            "tenant_schema": "tenant1",
        }

    def test_process_product_gap_round_dispatches_complete_when_round_limit_hit(
        self, monkeypatch,
    ):
        company = _make_company()
        product = _make_product(company)
        pre_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_PRE,
            content=_specialist_content("pre"),
        )
        state = {}

        monkeypatch.setattr(
            "apps.companies.views._plan_slug_for_company",
            lambda company_arg: "starter",
        )
        monkeypatch.setattr(
            "apps.companies.views._gap_engine_product_limits",
            lambda plan_slug: {"max_rounds": 0, "max_followups": 3},
        )

        def fake_set(pre_dna_arg, **kwargs):
            state.update(kwargs)

        def fake_delay(product_id, pre_dna_id, user_id, tenant_schema=None):
            state["delay"] = (product_id, pre_dna_id, user_id, tenant_schema)

        monkeypatch.setattr("apps.companies.views._set_product_gap_processing", fake_set)
        monkeypatch.setattr(tasks.generate_complete_product_dna, "delay", fake_delay)

        tasks.process_product_gap_round_task(product.id, pre_dna.id, current_round=1, user_id=7)

        assert state["status"] == "complete_pending"
        assert state["result"] == "complete"
        assert state["delay"] == (product.id, pre_dna.id, 7, None)

    def test_apply_specialist_feedback_task_creates_new_company_dna(self, monkeypatch):
        company = _make_company()
        product = _make_product(company)
        specialist_dna = ProductDNA.objects.create(
            product=product,
            version=1,
            dna_type=ProductDNA.TYPE_COMPLETE,
            content=_specialist_content("complete"),
        )
        current_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        current_dna.content = {
            "identita": "base",
            "_pending_specialist_feedback": {
                "product_id": product.id,
                "specialist_dna_id": specialist_dna.id,
                "selected_proposals": [{"target_layer": "nucleo_tecnico"}],
            },
        }
        current_dna.audit_hash = "abc123"
        current_dna.save(update_fields=["content", "audit_hash"])

        def fake_regenerate(
            company_arg, product_arg, company_dna_arg, specialist_dna_arg, proposals,
        ):
            assert proposals == [{"target_layer": "nucleo_tecnico"}]
            return {
                "identita": "base aggiornata",
                "nucleo_tecnico": specialist_dna_arg.content["specifiche"],
                "_pending_specialist_feedback": "remove-me",
            }

        monkeypatch.setattr(
            "apps.companies.views._regenerate_company_dna_from_specialist_feedback",
            fake_regenerate,
        )

        tasks.apply_specialist_feedback_task(company.id, current_dna.id)

        new_dna = company.dna_versions.get(version=2)
        assert new_dna.is_current is True
        assert new_dna.previous_hash == "abc123"
        assert "_pending_specialist_feedback" not in new_dna.content
        assert new_dna.audit_hash

    def test_run_consistency_audit_creates_issue_and_accumulated(self, monkeypatch):
        company = _make_company()
        company_dna = company.dna_versions.get(dna_type=CompanyDNA.TYPE_COMPLETE)
        company_dna.content["_consistency_audit_pending"] = {"scope": "periodic"}
        company_dna.save(update_fields=["content"])
        for index in range(3):
            product = _make_product(company, slug=f"canale-{index}", codice=f"CI-00{index}")
            product.status = Product.STATUS_ATTIVO
            product.save(update_fields=["status"])
            ProductDNA.objects.create(
                product=product,
                version=1,
                dna_type=ProductDNA.TYPE_COMPLETE,
                content=_specialist_content(f"active {index}"),
            )

        fake_result = SimpleNamespace(
            text=(
                '{"summary":"warning confini","issues":[{"severity":"high",'
                '"issue_type":"boundary","title":"Confine assolutizzato",'
                '"description":"Un vincolo specialista sembra generalizzato.",'
                '"recommendation":"Separare principio generale e limite prodotto.",'
                '"company_layer":"confini","product_layer":"vincoli",'
                '"evidence":{"products":["Canale Ispezionabile"]}}]}'
            ),
            tokens_in=10,
            tokens_out=10,
            cost=0,
            latency_ms=1,
        )

        def fake_generate(*args, **kwargs):
            return fake_result, json.loads(fake_result.text)

        monkeypatch.setattr(
            "apps.companies.tasks._generate_with_retry",
            fake_generate,
        )
        monkeypatch.setattr("apps.companies.tasks.get_llm_client", lambda: None)

        count = tasks._run_consistency_audit(company.id)

        assert count == 1
        issue = ConsistencyIssue.objects.get(company=company)
        assert issue.severity == ConsistencyIssue.SEVERITY_HIGH
        assert issue.company_layer == "confini"
        assert issue.product_layer == "vincoli"
        assert issue.status == ConsistencyIssue.STATUS_OPEN
        company_dna.refresh_from_db()
        assert "_consistency_audit_pending" not in company_dna.content
        assert company_dna.content["_accumulated"]["active_specialist_count"] == 3
        assert company_dna.content["_accumulated"]["last_consistency_audit"]["issue_count"] == 1
