# Implementation Plan: Brand Watchdog

## Overview

Implementação do sistema Brand Watchdog em Python 3.10+ async, seguindo a arquitetura modular definida no design. A implementação é incremental: começa com scaffolding e modelos de dados, avança pelos componentes de domínio (URL validation, Brand Registry, Target Site Manager), depois os componentes de infraestrutura (Crawler, Analyzer, Alert Service, Storage), e finaliza com a orquestração (Coordinator, Scheduler) e cleanup de retenção.

## Tasks

- [x] 1. Scaffolding do projeto e configuração base
  - [x] 1.1 Criar estrutura de diretórios e arquivos iniciais do projeto
    - Criar a árvore de diretórios conforme design: `brand_watchdog/`, `brand_watchdog/models/`, `brand_watchdog/crawler/`, `brand_watchdog/analyzer/`, `brand_watchdog/alerts/`, `brand_watchdog/registry/`, `brand_watchdog/storage/`, `brand_watchdog/coordinator/`, `brand_watchdog/scheduler/`, `brand_watchdog/utils/`
    - Criar todos os `__init__.py` em cada pacote
    - Criar `pyproject.toml` ou `requirements.txt` com dependências: sqlalchemy, playwright, boto3, apscheduler, tenacity, pyyaml, aiosmtplib
    - Criar `tests/` com subdiretórios `property/`, `unit/`, `integration/` e `conftest.py`
    - _Requirements: N/A (infraestrutura do projeto)_

  - [x] 1.2 Implementar Configuration Manager (`brand_watchdog/config.py`)
    - Criar dataclasses `CrawlerConfig`, `AnalyzerConfig`, `AlertConfig`, `ScheduleConfig`, `StorageConfig`, `AppConfig` conforme design
    - Implementar carregamento de configuração a partir de arquivo YAML com override por variáveis de ambiente
    - Validar `ScheduleConfig.interval_hours` entre 1 e 720, e retention periods entre 1 e 365
    - _Requirements: 5.2, 7.3, 8.3_

  - [x] 1.3 Escrever property tests para validação de configuração
    - **Property 10: Schedule Frequency Validation** — inteiros de -100 a 1000, aceita apenas [1, 720]
    - **Property 16: Retention Period Configuration Validation** — inteiros de -100 a 500, aceita apenas [1, 365]
    - **Validates: Requirements 5.2, 7.3, 8.3**

- [x] 2. Modelos de dados e banco de dados
  - [x] 2.1 Criar modelos SQLAlchemy (`brand_watchdog/models/entities.py`)
    - Implementar `TargetSiteModel`, `BrandAssetModel`, `MonitoringCycleModel`, `ScreenshotModel`, `DetectionResultModel`, `AlertLogModel` conforme design ER
    - Garantir indexes em `normalized_url` (unique), `content_hash` (unique), `expires_at`
    - _Requirements: 1.1, 1.4, 2.1, 2.3, 7.1, 8.1_

  - [x] 2.2 Criar dataclasses de domínio/DTOs (`brand_watchdog/models/dataclasses.py`)
    - Implementar `CaptureResult`, `DetectionResult`, `BoundingBox`, `BrandAsset`, `TargetSite`, `ValidationResult`, `CycleResult`, `SiteResult`, `QueryResult`
    - _Requirements: 4.4, 7.1_

  - [x] 2.3 Configurar engine e sessão do SQLAlchemy (`brand_watchdog/models/database.py`)
    - Criar `async_engine` e `async_session` factory configuráveis via `StorageConfig.database_url`
    - Implementar função `init_db()` para criação das tabelas
    - _Requirements: 7.1_

- [x] 3. Utilitários de validação e URL
  - [x] 3.1 Implementar URL Validator e Normalizer (`brand_watchdog/utils/validators.py`)
    - Implementar classe `URLValidator` com métodos `validate(url)` e `normalize(url)` conforme design
    - Validação: scheme http/https, hostname RFC 1123, max 2048 chars
    - Normalização: lowercase scheme/host, remove trailing slash, idempotente
    - _Requirements: 1.1, 1.3, 1.4, 1.5_

  - [x] 3.2 Escrever property tests para URL Validation
    - **Property 1: URL Validation Correctness** — strings aleatórias com schemes, hostnames e paths, aceita apenas URLs válidas
    - **Validates: Requirements 1.1, 1.3, 1.5**

  - [x] 3.3 Escrever property tests para URL Normalization
    - **Property 2: URL Normalization Idempotence** — URLs válidas com variações de case e trailing slashes, normalize(normalize(url)) == normalize(url)
    - **Validates: Requirements 1.4**

  - [x] 3.4 Implementar validadores de Brand Assets (`brand_watchdog/utils/validators.py`)
    - Validador de formato de imagem (PNG, JPG, SVG) e tamanho (≤ 5 MB)
    - Validador de texto de marca (2-256 chars, pelo menos 2 chars visíveis)
    - _Requirements: 2.1, 2.2, 2.4, 2.6_

  - [x] 3.5 Escrever property tests para Brand Text Validation
    - **Property 5: Brand Text Validation** — strings de 0-300 chars com mix de whitespace, aceita apenas strings com 2-256 chars e ≥2 chars visíveis
    - **Validates: Requirements 2.2, 2.6**

  - [x] 3.6 Escrever property tests para Image Format Validation
    - **Property 6: Image Format Validation** — file headers + tamanhos variados, aceita apenas PNG/JPG/SVG ≤ 5 MB
    - **Validates: Requirements 2.1, 2.4**

  - [x] 3.7 Implementar utilitário de hashing (`brand_watchdog/utils/hashing.py`)
    - SHA-256 para conteúdo de imagens e texto, usado para deduplicação
    - _Requirements: 2.5_

- [x] 4. Checkpoint - Verificar fundações
  - Ensure all tests pass, ask the user if questions arise.

- [x] 5. Brand Registry e Target Site Manager
  - [x] 5.1 Implementar Brand Registry (`brand_watchdog/registry/brand_registry.py`)
    - Métodos `register_logo(image_data, filename)`, `register_text(text)`, `get_all_assets()`, `remove_asset(asset_id)`
    - Deduplicação via content_hash, validação de formato/tamanho/texto antes do registro
    - Armazenamento de logos no filesystem com path configurável
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 5.2 Escrever property tests para Brand Registry
    - **Property 3: Brand Asset Registration Round-Trip** — assets válidos registrados devem aparecer em get_all_assets()
    - **Property 4: Brand Asset Deduplication** — registro duplicado (mesmo content) deve ser rejeitado
    - **Validates: Requirements 2.3, 2.5**

  - [x] 5.3 Implementar Target Site Manager (`brand_watchdog/registry/target_site_manager.py`)
    - Métodos `register(url)`, `remove(site_id)`, `list_all()`
    - Validação e normalização de URL antes do registro
    - Controle de duplicatas via normalized_url unique
    - Limite máximo de 200 Target Sites por conta
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

  - [x] 5.4 Escrever unit tests para Target Site Manager
    - Testar registro válido, remoção, listagem, duplicata rejeitada, limite de 200 atingido, URL inválida
    - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5, 1.6, 1.7_

- [x] 6. Crawler com Playwright
  - [x] 6.1 Implementar Crawler (`brand_watchdog/crawler/crawler.py`)
    - Classe `Crawler` com método `capture(target_url) -> CaptureResult`
    - Navegação com Playwright async, viewport 1280px
    - Scroll incremental para lazy-loading com wait_for_load_state("networkidle")
    - Screenshot full-page com `full_page=True`
    - Truncamento em 20,000px com log de warning
    - Timeout de 60 segundos por página
    - Tratamento de HTTP errors (4xx/5xx): log + skip
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

  - [x] 6.2 Escrever property test para Screenshot Height Truncation
    - **Property 7: Screenshot Height Truncation** — páginas com alturas variadas (1-50000px), trunca em 20000px
    - **Validates: Requirements 3.6**

  - [x] 6.3 Escrever unit tests para Crawler
    - Testar timeout handling, HTTP 4xx/5xx skip, network idle wait, lazy scroll
    - Usar mocks de Playwright para simular cenários
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 7. Screenshot Store
  - [x] 7.1 Implementar Screenshot Store (`brand_watchdog/storage/screenshot_store.py`)
    - Métodos `store(png_bytes, target_site_id, cycle_id) -> ScreenshotModel`, `retrieve(screenshot_id) -> bytes`, `cleanup_expired() -> int`
    - Armazenamento como PNG no filesystem com path configurável
    - Associação com Target_Site URL e timestamp UTC (precisão de segundos)
    - Retry com exponential backoff (1s, 2s, 4s) para falhas de escrita
    - Cálculo de `expires_at` baseado em `screenshot_retention_days`
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5_

  - [x] 7.2 Escrever property test para Screenshot Storage Round-Trip
    - **Property 19: Screenshot Storage Round-Trip** — PNG bytes aleatórios, store + retrieve produz bytes idênticos
    - **Validates: Requirements 8.1, 8.5**

- [x] 8. Analyzer com AWS Bedrock
  - [x] 8.1 Implementar Bedrock Client (`brand_watchdog/analyzer/bedrock_client.py`)
    - Classe `BedrockClient` com método `invoke_model(image_bytes, prompt) -> dict`
    - Retry com Tenacity: 3 tentativas, exponential backoff (2s, 4s, 8s)
    - Timeout de 60 segundos por request
    - Construção do payload com imagem base64 e prompt
    - _Requirements: 4.1, 4.6, 4.7, 4.8_

  - [x] 8.2 Implementar Analyzer (`brand_watchdog/analyzer/analyzer.py`)
    - Classe `Analyzer` com método `analyze(screenshot_path, brand_assets) -> list[DetectionResult]`
    - Construção de prompt multimodal com lista de logos e textos a detectar
    - Parsing de resposta JSON do Bedrock em `DetectionResult` objects
    - Filtragem por confidence threshold (≥ 60 como confirmado)
    - Tratamento de erros: log + marca análise como incompleta após retry exhaustion
    - _Requirements: 4.1, 4.2, 4.3, 4.4, 4.5, 4.6, 4.7, 4.8_

  - [x] 8.3 Escrever property test para Bedrock Response Parsing
    - **Property 8: Bedrock Response Parsing** — JSON responses com arrays de detecções variadas, parser extrai corretamente
    - **Validates: Requirements 4.4**

  - [x] 8.4 Escrever property test para Confidence Threshold Classification
    - **Property 9: Confidence Threshold Classification** — DetectionResults com confidence 0-100, classifica corretamente conforme threshold
    - **Validates: Requirements 4.5**

  - [x] 8.5 Escrever unit tests para Analyzer
    - Testar retry exhaustion, parsing de resposta malformada, timeout, prompt building
    - _Requirements: 4.1, 4.4, 4.5, 4.6, 4.7, 4.8_

- [x] 9. Checkpoint - Verificar componentes individuais
  - Ensure all tests pass, ask the user if questions arise.

- [x] 10. Detection Store
  - [x] 10.1 Implementar Detection Store (`brand_watchdog/storage/detection_store.py`)
    - Métodos `save(detection)`, `query(filters, page, page_size)`, `cleanup_expired()`, `get_previous_cycle_detections(target_url)`
    - Persistência com retry (3 tentativas, exponential backoff 1s, 2s, 4s)
    - Paginação com máximo 100 resultados por página, ordem cronológica reversa
    - Cálculo de `expires_at` baseado em `detection_retention_days`
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 7.6_

  - [x] 10.2 Escrever property test para Detection Result Persistence Round-Trip
    - **Property 15: Detection Result Persistence Round-Trip** — DetectionResults completos, save + query produz dados idênticos
    - **Validates: Requirements 7.1**

  - [x] 10.3 Escrever property test para Query Filtering Correctness
    - **Property 18: Query Filtering Correctness** — conjuntos de detecções + filtros, resultados respeitam todos os filtros aplicados e paginação
    - **Validates: Requirements 7.5**

  - [x] 10.4 Escrever property test para Expired Item Cleanup
    - **Property 17: Expired Item Cleanup** — itens com expiration variada, cleanup remove apenas expirados
    - **Validates: Requirements 7.4, 8.4**

- [x] 11. Alert Service
  - [x] 11.1 Implementar Alert Service (`brand_watchdog/alerts/alert_service.py`)
    - Classe `AlertService` com método `send_alert(detection, recipients) -> bool`
    - Supressão de alertas duplicados: verifica ciclo anterior (mesmo target_url, match_type, bounding_box com tolerância 5%)
    - Formatação de email com URL, match type, confidence, descrição, timestamp ISO 8601
    - _Requirements: 6.1, 6.2, 6.5, 6.6, 6.7_

  - [x] 11.2 Implementar Email Providers (`brand_watchdog/alerts/email_providers.py`)
    - Classe `SESProvider` para envio via AWS SES com retry (3 tentativas, intervalo 30s)
    - Classe `SMTPProvider` para envio via SMTP com retry (3 tentativas, intervalo 30s)
    - Seleção de provider via `AlertConfig.provider`
    - Log de falhas com destinatário e URL após retry exhaustion
    - _Requirements: 6.3, 6.4_

  - [x] 11.3 Escrever property test para Alert Email Content Completeness
    - **Property 13: Alert Email Content Completeness** — DetectionResults variados, email contém todos os campos obrigatórios
    - **Validates: Requirements 6.2**

  - [x] 11.4 Escrever property test para Duplicate Alert Suppression
    - **Property 14: Duplicate Alert Suppression** — pares de detecções (current vs previous), suprime corretamente duplicatas
    - **Validates: Requirements 6.7**

  - [x] 11.5 Escrever unit tests para Alert Service
    - Testar seleção de provider (SES vs SMTP), retry on send failure, supressão de duplicatas
    - _Requirements: 6.1, 6.3, 6.4, 6.7_

- [x] 12. Monitoring Coordinator
  - [x] 12.1 Implementar Monitoring Coordinator (`brand_watchdog/coordinator/coordinator.py`)
    - Classe `MonitoringCoordinator` com método `run_cycle() -> CycleResult`
    - Método `_process_site(target_site)`: capture → analyze → alert
    - Lock de ciclo: verifica se ciclo anterior está em execução, pula se sim
    - Criação de `MonitoringCycleModel` no início, atualização com stats no final
    - Processamento de todos os Target Sites ativos, registrando sucesso/falha individual
    - Log de ciclo completo: start_time, end_time, sites_processed, sites_failed, detections_found
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_

  - [x] 12.2 Escrever property test para Cycle Processes All Sites
    - **Property 11: Cycle Processes All Sites** — conjuntos de 1-50 target sites (mockados), todos são processados
    - **Validates: Requirements 5.3**

  - [x] 12.3 Escrever property test para Cycle Result Completeness
    - **Property 12: Cycle Result Completeness** — ciclos com mix de sucesso/falha, resultado contém todos os campos e contagens corretas
    - **Validates: Requirements 5.6**

  - [x] 12.4 Escrever unit tests para Coordinator
    - Testar cycle lock, concurrent cycle skip, site failure handling, stats update
    - _Requirements: 5.1, 5.3, 5.4, 5.5, 5.6_

- [x] 13. Scheduler
  - [x] 13.1 Implementar Scheduler (`brand_watchdog/scheduler/scheduler.py`)
    - Wrapper sobre APScheduler com intervalo configurável (1-720 horas)
    - Trigger do `MonitoringCoordinator.run_cycle()` no intervalo definido
    - Métodos `start()`, `stop()`, `update_interval(hours)`
    - _Requirements: 5.1, 5.2_

  - [x] 13.2 Implementar entry point (`brand_watchdog/main.py`)
    - Inicialização do `AppConfig` via YAML + env vars
    - Setup do banco de dados (init_db)
    - Instanciação de todos os componentes com injeção de dependências
    - Start do Scheduler
    - Graceful shutdown com signal handling
    - _Requirements: 5.1_

- [x] 14. Retention Cleanup
  - [x] 14.1 Implementar job de cleanup agendado
    - Registrar job no Scheduler para rodar cleanup de detecções e screenshots expirados (diariamente)
    - Executar em batches de 100 itens para não sobrecarregar o banco
    - Remover arquivos físicos de screenshots junto com registros do banco
    - _Requirements: 7.3, 7.4, 8.3, 8.4_

- [x] 15. Checkpoint - Verificar integração dos componentes
  - Ensure all tests pass, ask the user if questions arise.

- [x] 16. Integration Tests
  - [x] 16.1 Escrever integration tests para fluxo de crawl
    - Teste de captura de página local com conteúdo lazy-loaded usando Playwright real
    - _Requirements: 3.1, 3.2, 3.3_

  - [x] 16.2 Escrever integration tests para fluxo de análise
    - Fluxo completo Analyzer + Bedrock mock: screenshot → análise → DetectionResults
    - _Requirements: 4.1, 4.4, 4.5_

  - [x] 16.3 Escrever integration tests para fluxo de alerta
    - Alert Service + SES/SMTP mock: detecção → email enviado com conteúdo correto
    - _Requirements: 6.1, 6.2, 6.3_

  - [x] 16.4 Escrever integration test para ciclo completo
    - Ciclo de monitoramento end-to-end com todos os componentes mockados externamente
    - Verifica: captura, análise, alerta, persistência, stats do ciclo
    - _Requirements: 5.1, 5.3, 5.6_

- [x] 17. Final checkpoint - Verificar sistema completo
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marcadas com `*` são opcionais e podem ser puladas para um MVP mais rápido
- Cada task referencia requirements específicos para rastreabilidade
- Checkpoints garantem validação incremental ao longo da implementação
- Property tests validam propriedades universais de corretude (Hypothesis com min 100 examples)
- Unit tests cobrem exemplos específicos e edge cases
- Integration tests verificam o fluxo entre componentes
- O projeto usa Python 3.10+ async com type hints (PEP 8)
- Todas as dependências externas (Bedrock, SES, SMTP, Playwright) devem ser mockadas nos testes

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1"] },
    { "id": 1, "tasks": ["1.2", "2.1", "2.2"] },
    { "id": 2, "tasks": ["1.3", "2.3", "3.1", "3.4", "3.7"] },
    { "id": 3, "tasks": ["3.2", "3.3", "3.5", "3.6"] },
    { "id": 4, "tasks": ["5.1", "5.3"] },
    { "id": 5, "tasks": ["5.2", "5.4", "6.1", "7.1"] },
    { "id": 6, "tasks": ["6.2", "6.3", "7.2", "8.1"] },
    { "id": 7, "tasks": ["8.2"] },
    { "id": 8, "tasks": ["8.3", "8.4", "8.5", "10.1"] },
    { "id": 9, "tasks": ["10.2", "10.3", "10.4", "11.1"] },
    { "id": 10, "tasks": ["11.2"] },
    { "id": 11, "tasks": ["11.3", "11.4", "11.5", "12.1"] },
    { "id": 12, "tasks": ["12.2", "12.3", "12.4", "13.1"] },
    { "id": 13, "tasks": ["13.2", "14.1"] },
    { "id": 14, "tasks": ["16.1", "16.2", "16.3", "16.4"] }
  ]
}
```
