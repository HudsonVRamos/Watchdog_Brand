# Implementation Plan: MVP.1 SKY+/Amazon Compliance Validation

## Overview

Transformação do módulo Analyzer do Brand Watchdog de "detecção de presença de marca" para validação de compliance da parceria SKY+/Amazon Prime. A implementação segue uma abordagem incremental: modelos de dados → exceções → componentes internos → componente principal → integração com coordinator → notificação por email.

## Tasks

- [ ] 1. Definir modelos de dados e hierarquia de exceções
  - [ ] 1.1 Criar dataclasses ComplianceRuleResult e ComplianceReport
    - Criar/modificar `brand_watchdog/models/dataclasses.py`
    - Implementar `ComplianceRuleResult` com campos: rule_id (str), status (str: "PASS"|"FAIL"|"NOT_APPLICABLE"), confidence (int 0-100), description (str max 1024 chars)
    - Implementar `ComplianceReport` com campos: target_url, analyzed_at (datetime), overall_status (str: "compliant"|"non_compliant"|"error"), rule_results (list[ComplianceRuleResult]), screenshot_ref_id (str), cycle_id (str)
    - Implementar método de derivação de `overall_status`: "non_compliant" se qualquer regra tem status "FAIL", senão "compliant"
    - Implementar serialização round-trip (to_dict / from_dict)
    - Definir constante `COMPLIANCE_RULES` com as 6 regras configuradas
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.5, 9.1, 9.2_

  - [ ]* 1.2 Escrever property test para serialização round-trip
    - **Property 1: ComplianceReport serialization round-trip**
    - Usar Hypothesis para gerar ComplianceReport válidos e verificar que serializar → deserializar produz objeto idêntico
    - Criar generators: `compliance_rule_result()` e `compliance_report()`
    - **Validates: Requirements 7.5**

  - [ ]* 1.3 Escrever property test para derivação de overall_status
    - **Property 2: Overall compliance status derivation**
    - Usar Hypothesis para gerar listas de ComplianceRuleResult e verificar que overall_status é "non_compliant" sse pelo menos uma regra tem status "FAIL"
    - **Validates: Requirements 7.3, 7.4**

  - [ ] 1.4 Criar hierarquia de exceções de compliance
    - Criar `brand_watchdog/analyzer/compliance_exceptions.py`
    - Implementar `ComplianceError(Exception)` como classe base
    - Implementar `AnalysisIncompleteError(ComplianceError)` para falhas de Bedrock ou screenshot ilegível
    - Implementar `ComplianceParseError(ComplianceError)` para respostas não parseáveis
    - Implementar `CompliancePersistenceError(ComplianceError)` para falhas de persistência após retries
    - _Requirements: 7.6, 9.4_

- [ ] 2. Implementar ComplianceReportParser
  - [ ] 2.1 Criar ComplianceReportParser para parsing de respostas Bedrock
    - Criar `brand_watchdog/analyzer/compliance_report_parser.py`
    - Implementar método `parse_response(raw_json: dict) -> ComplianceReport` que valida e transforma a resposta do Bedrock
    - Validar presença da chave "compliance_results" no JSON
    - Validar que cada rule result contém: rule_id, status, confidence, description
    - Validar que status está em {"PASS", "FAIL", "NOT_APPLICABLE"}
    - Validar que confidence é int 0-100
    - Validar que description tem ≤ 1024 caracteres
    - Validar que todas as 6 regras configuradas estão presentes na resposta
    - Raise `ComplianceParseError` para respostas inválidas com log do tamanho e timestamp
    - _Requirements: 7.1, 7.2, 7.6, 7.7_

  - [ ]* 2.2 Escrever property test para handling de respostas malformadas
    - **Property 3: Malformed Bedrock response error handling**
    - Usar Hypothesis para gerar JSON strings que não conformam ao schema esperado e verificar que ComplianceReportParser retorna erro sem produzir ComplianceReport parcial
    - Criar generator: `bedrock_compliance_response()` (válidas e inválidas)
    - **Validates: Requirements 7.6, 7.7**

  - [ ]* 2.3 Escrever property test para completude da estrutura do report
    - **Property 9: Report structure completeness**
    - Usar Hypothesis para gerar respostas Bedrock válidas com todas as regras e verificar que o ComplianceReport parseado contém exatamente 6 ComplianceRuleResult, cada um com rule_id válido, status válido, confidence 0-100, e description ≤ 1024
    - **Validates: Requirements 7.1, 7.2**

- [ ] 3. Implementar CompliancePromptBuilder
  - [ ] 3.1 Criar CompliancePromptBuilder com carregamento de imagens de referência
    - Criar `brand_watchdog/analyzer/compliance_prompt_builder.py`
    - Implementar classe `CompliancePromptBuilder` com `REFERENCE_IMAGES_DIR` e mapeamento de imagens
    - Implementar `build_prompt(screenshot_path: Path) -> PromptPayload`
    - Implementar `_load_reference_images()` que carrega imagens de `watchdog_rules/SKY_Amazon_Imagens/`
    - Implementar `_build_rules_text()` com exatamente 5 seções de regras: facilitator_role, logo_application, content_separation, naming_pricing, kv_integrity
    - Log warning para cada imagem de referência ausente ou ilegível
    - Continuar análise mesmo com todas as imagens ausentes (screenshot + rules text only)
    - Raise `AnalysisIncompleteError` se screenshot não é legível
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 6.7, 6.8_

  - [ ]* 3.2 Escrever property test para resiliência a imagens ausentes
    - **Property 8: Prompt builder resilience to missing reference images**
    - Usar Hypothesis para gerar combinações de arquivos presentes/ausentes (0 a 3) e verificar que PromptPayload é sempre válido com screenshot + imagens disponíveis + texto completo das 5 regras
    - Criar generator: `reference_image_availability()`
    - **Validates: Requirements 6.7, 6.8**

  - [ ]* 3.3 Escrever unit tests para CompliancePromptBuilder
    - Testar que prompt contém exatamente 5 seções de regras
    - Testar que screenshot é a primeira imagem no payload
    - Testar labels corretos para cada imagem de referência
    - Testar cenário com todas as imagens presentes
    - Testar cenário com nenhuma imagem de referência presente
    - _Requirements: 6.1, 6.2, 6.3, 6.4, 6.5, 6.6_

- [ ] 4. Checkpoint - Validar modelos e componentes internos
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 5. Estender BedrockClient para suporte multi-imagem
  - [ ] 5.1 Implementar invoke_model_multi e validação de payload
    - Modificar `brand_watchdog/analyzer/bedrock_client.py`
    - Adicionar método `invoke_model_multi(images: list[tuple[bytes, str]], prompt: str) -> dict`
    - Implementar `_build_multi_image_payload()` que constrói payload com text blocks (labels) antes de cada image block
    - Implementar `_validate_payload_size(images)` que filtra imagens: skip individual > 5MB, fallback para screenshot only se total > 20MB
    - Manter método `invoke_model()` original para backwards compatibility
    - Manter retry logic existente (3 tentativas, backoff 2s, 4s, 8s) e timeout 60s
    - Labels: "screenshot_under_analysis", "approved_art_reference", "correct_logo_reference", "wrong_logo_example"
    - _Requirements: 10.1, 10.2, 10.3, 10.4, 10.5_

  - [ ]* 5.2 Escrever property test para construção de payload multi-imagem
    - **Property 6: Multi-image payload construction with labels**
    - Usar Hypothesis para gerar listas de 1-5 tuples (image_bytes, label) dentro dos limites e verificar que o payload construído contém cada imagem com seu label na ordem correta
    - Criar generator: `image_with_label()`
    - **Validates: Requirements 10.1, 10.2**

  - [ ]* 5.3 Escrever property test para filtragem por tamanho de imagem
    - **Property 7: Image size filtering**
    - Usar Hypothesis para gerar listas de imagens com tamanhos variáveis e verificar: (a) total > 20MB → apenas screenshot; (b) individual > 5MB → skip dessa imagem; (c) resultado sempre contém pelo menos o screenshot
    - **Validates: Requirements 10.4, 10.5**

- [ ] 6. Implementar ComplianceAnalyzer
  - [ ] 6.1 Criar ComplianceAnalyzer com fluxo principal de análise
    - Criar `brand_watchdog/analyzer/compliance_analyzer.py`
    - Implementar classe `ComplianceAnalyzer` com injeção de dependências (config, bedrock_client, prompt_builder)
    - Implementar `analyze_compliance(screenshot_path, target_url, screenshot_ref_id, cycle_id) -> ComplianceReport`
    - Orquestrar fluxo: build_prompt → invoke_model_multi → parse_response → derive overall_status
    - Implementar persistência de violações: para cada regra com status "FAIL", criar DetectionResult com match_type=rule_id, confidence=confidence, bbox zeros, description, expires_at calculado
    - Retry de persistência 3x com backoff exponencial, raise `CompliancePersistenceError` se todos falharem
    - _Requirements: 7.1, 7.2, 7.3, 7.4, 7.6, 7.7, 9.1, 9.2, 9.3, 9.4, 9.5_

  - [ ]* 6.2 Escrever property test para mapeamento FAIL → DetectionResult
    - **Property 5: FAIL rule mapping to DetectionResult**
    - Usar Hypothesis para gerar ComplianceRuleResult com status "FAIL" e verificar que o DetectionResult produzido tem match_type=rule_id, confidence correto, bounding box zeros, description correta, e expires_at=analyzed_at + detection_retention_days
    - **Validates: Requirements 9.3, 9.5**

  - [ ]* 6.3 Escrever unit tests para ComplianceAnalyzer
    - Testar fluxo completo com mock de BedrockClient retornando resposta válida
    - Testar handling de resposta inválida do Bedrock (ComplianceParseError)
    - Testar persistência de violações com DetectionStore mockado
    - Testar que apenas regras FAIL geram DetectionResult
    - Testar CompliancePersistenceError após exaustão de retries
    - _Requirements: 7.1, 7.6, 9.3, 9.4_

- [ ] 7. Checkpoint - Validar analyzer completo
  - Ensure all tests pass, ask the user if questions arise.

- [ ] 8. Implementar ComplianceEmailNotifier
  - [ ] 8.1 Criar ComplianceEmailNotifier com formatação e envio de relatórios
    - Criar `brand_watchdog/alerts/compliance_email_notifier.py`
    - Implementar classe `ComplianceEmailNotifier` com injeção de EmailProvider
    - Implementar `send_compliance_report(report, recipients) -> bool`
    - Implementar `_format_compliance_email(report) -> tuple[str, str]` (subject, body)
    - Formatar email com: ISP URL, timestamp ISO 8601, overall status, e para cada regra: rule_id, status, confidence score
    - Para non_compliant: incluir lista de regras falhadas com descriptions e confidence
    - Para compliant: incluir confirmação de que todas as regras passaram
    - Implementar `_send_with_retry(recipient, subject, body) -> bool` com retry 3x intervalo 30s
    - Se todos retries falharem para um recipient: log error e continuar para próximos recipients
    - _Requirements: 8.1, 8.2, 8.3, 8.4, 8.5, 8.6_

  - [ ]* 8.2 Escrever property test para completude do email
    - **Property 4: Email formatting completeness**
    - Usar Hypothesis para gerar ComplianceReport (compliant e non_compliant) e verificar que o email formatado contém: target URL, timestamp ISO 8601, overall status, e para cada regra: rule_id, status, confidence
    - **Validates: Requirements 8.2, 8.3, 8.4**

  - [ ]* 8.3 Escrever unit tests para ComplianceEmailNotifier
    - Testar formatação de email para report compliant
    - Testar formatação de email para report non_compliant
    - Testar retry logic com mock provider que falha nas primeiras tentativas
    - Testar isolamento entre recipients (falha em um não bloqueia outros)
    - Testar que exatamente 1 email é enviado por ISP por ciclo
    - _Requirements: 8.1, 8.2, 8.3, 8.5, 8.6_

- [ ] 9. Integrar com MonitoringCoordinator
  - [ ] 9.1 Refatorar MonitoringCoordinator para usar componentes de compliance
    - Modificar `brand_watchdog/coordinator/coordinator.py`
    - Substituir `Analyzer` por `ComplianceAnalyzer` na injeção de dependências
    - Substituir `AlertService` por `ComplianceEmailNotifier`
    - Refatorar `_process_site()` para: chamar `analyze_compliance()` → coletar ComplianceReport → chamar `send_compliance_report()`
    - Manter tratamento de erro por site: se um site falhar, log error e continuar processando demais sites
    - Enviar email consolidado independente do status (compliant ou non_compliant)
    - _Requirements: 1.1, 8.1, 9.1_

  - [ ]* 9.2 Escrever integration tests para fluxo completo
    - Testar ciclo end-to-end: capture → analyze → persist → notify com mocks de serviços externos
    - Testar BedrockClient com mock boto3
    - Testar persistência com mock de DetectionStore
    - Testar email com mock EmailProvider
    - Testar cenário com falha em um site e continuidade para demais
    - _Requirements: 1.1, 7.1, 8.1, 9.1_

- [ ] 10. Final checkpoint - Validação completa
  - Ensure all tests pass, ask the user if questions arise.

## Notes

- Tasks marcadas com `*` são opcionais e podem ser puladas para um MVP mais rápido
- Cada task referencia requisitos específicos para rastreabilidade
- Checkpoints garantem validação incremental
- Property tests validam propriedades universais de corretude (Hypothesis com min 100 exemplos)
- Unit tests validam exemplos específicos e edge cases
- O projeto já usa Hypothesis — generators devem seguir o padrão existente em `.hypothesis/`
- A infraestrutura existente (ECS Fargate, Playwright, SES, Aurora PostgreSQL) não é modificada
- O código original do Analyzer permanece no repositório para referência

## Task Dependency Graph

```json
{
  "waves": [
    { "id": 0, "tasks": ["1.1", "1.4"] },
    { "id": 1, "tasks": ["1.2", "1.3", "2.1"] },
    { "id": 2, "tasks": ["2.2", "2.3", "3.1"] },
    { "id": 3, "tasks": ["3.2", "3.3", "5.1"] },
    { "id": 4, "tasks": ["5.2", "5.3", "6.1"] },
    { "id": 5, "tasks": ["6.2", "6.3", "8.1"] },
    { "id": 6, "tasks": ["8.2", "8.3", "9.1"] },
    { "id": 7, "tasks": ["9.2"] }
  ]
}
```
