# Requirements Document

## Introduction

Este documento define os requisitos para o MVP.1 do sistema Brand Watchdog, transformando o analyzer de "detecção de presença de marca" para um **sistema de validação de compliance da parceria SKY+ / Amazon Prime**. O foco é monitorar websites de ISPs (Internet Service Providers) abaixo dos integradores, verificando se qualquer comunicação envolvendo a marca Amazon cumpre as regras definidas pelo Marketing da parceria SKY+/Amazon.

O sistema existente (ECS Fargate, Playwright, Bedrock Claude, SES) permanece inalterado na infraestrutura. A transformação ocorre exclusivamente no módulo Analyzer: novo prompt com regras de compliance, uso de imagens de referência para comparação visual, e estruturação de resultados por regra (pass/fail por regra de compliance).

## Glossary

- **Compliance_Analyzer**: Módulo do sistema responsável por analisar screenshots de ISPs e validar conformidade com as regras da parceria SKY+/Amazon Prime via AWS Bedrock Claude.
- **Compliance_Rule**: Regra individual definida pelo Marketing que deve ser validada em cada comunicação de ISP. Cada regra possui um identificador e critérios de aprovação/reprovação.
- **Compliance_Report**: Resultado estruturado da análise contendo o status pass/fail de cada Compliance_Rule para um dado screenshot.
- **Reference_Image**: Imagem oficial fornecida pelo Marketing para comparação visual (logos corretos, artes aprovadas, exemplos de erro).
- **ISP_Website**: Website de um Internet Service Provider abaixo dos integradores que comunica a parceria SKY+/Amazon Prime.
- **KV (Key_Visual)**: Material visual oficial da campanha SKY+/Amazon Prime, incluindo logos, artes aprovadas e elementos gráficos oficiais.
- **SKY_Plus_Logo**: Logo oficial do SKY+ com Amazon Prime, aplicado com barra separadora conforme guidelines.
- **Bedrock_Client**: Componente existente do sistema que invoca o modelo Claude via AWS Bedrock Runtime.
- **Email_Notifier**: Componente do sistema responsável por enviar relatórios de compliance por email via AWS SES.
- **Compliance_Prompt_Builder**: Componente responsável por construir o prompt multimodal contendo regras de compliance, imagens de referência e screenshot para análise.

## Requirements

### Requirement 1: Validação de SKY+ como Facilitador

**User Story:** As a brand compliance analyst, I want the system to verify that all ISP communications make clear that access to Amazon Prime services is through SKY+, so that the facilitator role of SKY+ is always communicated correctly.

#### Acceptance Criteria

1. WHEN a screenshot of an ISP_Website is analyzed, THE Compliance_Analyzer SHALL identify all visible mentions of Amazon Prime services (including "Amazon Prime", "Prime Video", "Amazon Music", "Prime Gaming", "Prime Reading") and verify that each mention is associated on the same page with a reference to SKY+ as the facilitator (such as "SKY+", "através do SKY+", "via SKY+", "SKY+ com Amazon Prime incluso", or the SKY_Plus_Logo).
2. IF one or more Amazon Prime service mentions are found without an associated SKY+ facilitator reference on the same page, THEN THE Compliance_Analyzer SHALL report a "FAIL" status for the "facilitator_role" Compliance_Rule with a description identifying which Amazon Prime mention lacks the SKY+ facilitator context.
3. IF all detected Amazon Prime service mentions on the page are associated with a SKY+ facilitator reference, THEN THE Compliance_Analyzer SHALL report a "PASS" status for the "facilitator_role" Compliance_Rule.
4. IF no mention of Amazon Prime services is detected on the ISP_Website screenshot, THEN THE Compliance_Analyzer SHALL report a "NOT_APPLICABLE" status for the "facilitator_role" Compliance_Rule indicating that no Amazon Prime content was found to evaluate.

### Requirement 2: Validação da Ordem de Aplicação de Logos

**User Story:** As a brand compliance analyst, I want the system to verify logo application order and formatting rules, so that the visual hierarchy of the partnership is maintained.

#### Acceptance Criteria

1. WHEN logos are detected on the ISP_Website, THE Compliance_Analyzer SHALL verify that the SKY_Plus_Logo with Amazon Prime appears FIRST in left-to-right reading order, before any other Prime service logos (Amazon Music, Prime Gaming, Prime Reading), and SHALL report a "FAIL" status for the "logo_application" Compliance_Rule if the order is incorrect.
2. WHEN logos are detected on the ISP_Website, THE Compliance_Analyzer SHALL verify that logos are separated by a vertical bar separator with spacing between them, and SHALL report a "FAIL" status for the "logo_application" Compliance_Rule if the separator is missing or logos are directly adjacent without spacing.
3. WHEN a logo is detected inside a sentence, modified from its original proportions or colors, tilted from its original horizontal orientation, or placed on a patterned or busy background that reduces logo legibility, THE Compliance_Analyzer SHALL report a "FAIL" status for the "logo_application" Compliance_Rule.
4. WHEN visual effects (light effects, shadows, filters) are detected overlapping or immediately adjacent to a logo, or when the logo color has been changed from the official Reference_Image colors, THE Compliance_Analyzer SHALL report a "FAIL" status for the "logo_effects" Compliance_Rule.
5. WHEN all logo application rules are satisfied, THE Compliance_Analyzer SHALL report a "PASS" status for the "logo_application" and "logo_effects" Compliance_Rules.
6. IF no logos related to the SKY+/Amazon Prime partnership are detected on the ISP_Website, THEN THE Compliance_Analyzer SHALL skip the "logo_application" and "logo_effects" Compliance_Rules and not include them in the Compliance_Report for that page.

### Requirement 3: Validação de Separação Visual de Conteúdo

**User Story:** As a brand compliance analyst, I want the system to verify that partner content is visually separated from SKY+ content, so that brand identities remain distinct and professional.

#### Acceptance Criteria

1. WHEN partner content (visual identity, typography, images, prices, offers, advertising messages) is detected on the ISP_Website, THE Compliance_Analyzer SHALL verify that the content is visually separated from the SKY+/Amazon art by means of distinct blocks/sections, device mockups, or the SKY+ snake graphic element.
2. WHEN content elements overlap or are placed over logos or KV content without a clear visual boundary, THE Compliance_Analyzer SHALL report a "FAIL" status for the "content_separation" Compliance_Rule with a description indicating which partner element violates the separation and where the overlap occurs.
3. WHEN partner content is separated from SKY+/Amazon art by at least one of the accepted separation methods (distinct blocks/sections, device mockups, or the SKY+ snake graphic element) and no overlap is detected, THE Compliance_Analyzer SHALL report a "PASS" status for the "content_separation" Compliance_Rule.
4. IF no partner content is detected on the ISP_Website screenshot, THEN THE Compliance_Analyzer SHALL report a "PASS" status for the "content_separation" Compliance_Rule with a description indicating that no partner content was found requiring separation validation.
5. WHEN SKY+/Amazon partnership art is found used on a public-facing ISP_Website page (not internal communication), THE Compliance_Analyzer SHALL report a "FAIL" status for the "content_separation" Compliance_Rule with a description indicating that partnership arts require prior Amazon approval for website usage.

### Requirement 4: Validação de Nomenclatura e Preços

**User Story:** As a brand compliance analyst, I want the system to verify correct naming conventions and pricing rules, so that the partnership terms are communicated accurately.

#### Acceptance Criteria

1. WHEN the app name is mentioned on the ISP_Website, THE Compliance_Analyzer SHALL verify that the text matches "SKY+ com Amazon Prime incluso" using case-insensitive comparison, and IF the text does not match, THEN THE Compliance_Analyzer SHALL report a "FAIL" status for the "naming_pricing" Compliance_Rule with a description indicating the incorrect name found.
2. WHEN a price for the SKY+/Amazon Prime combo is detected on the ISP_Website, THE Compliance_Analyzer SHALL verify that the numeric value is not below R$80.00, and IF the value is below R$80.00, THEN THE Compliance_Analyzer SHALL report a "FAIL" status for the "naming_pricing" Compliance_Rule with a description indicating the non-compliant price found.
3. WHEN terms "grátis", "gratuito", "de graça", "sem custo", "sem custos", "a custo zero", or "100% grátis" are used in the context of the SKY+/Amazon Prime partnership, THE Compliance_Analyzer SHALL report a "FAIL" status for the "naming_pricing" Compliance_Rule with a description indicating the prohibited term found.
4. WHEN naming and pricing rules are all satisfied (correct app name, price not below R$80.00, and no prohibited "free" terms detected in partnership context), THE Compliance_Analyzer SHALL report a "PASS" status for the "naming_pricing" Compliance_Rule.

### Requirement 5: Validação de Integridade do Key Visual

**User Story:** As a brand compliance analyst, I want the system to verify KV integrity, so that official campaign materials are never altered or misused by ISPs.

#### Acceptance Criteria

1. WHEN a KV element is detected on the ISP_Website, THE Compliance_Analyzer SHALL verify that the official KV has not been altered in crop, color, position, or effects by comparing the detected element against the Reference_Image for approved arts and reporting any visually perceptible deviation as a "FAIL" for the "kv_integrity" Compliance_Rule.
2. WHEN partner logos are placed over or overlapping KV content, THE Compliance_Analyzer SHALL report a "FAIL" status for the "kv_integrity" Compliance_Rule.
3. WHEN the SKY_Plus_Logo is found horizontally mirrored, vertically inverted, rotated to any degree from its intended horizontal orientation, or placed outside the designated logo area defined in the Reference_Image, THE Compliance_Analyzer SHALL report a "FAIL" status for the "kv_integrity" Compliance_Rule.
4. WHEN filters, shadows, or visual effects are applied to the SKY_Plus_Logo within the KV, THE Compliance_Analyzer SHALL report a "FAIL" status for the "kv_integrity" Compliance_Rule.
5. WHEN Amazon partnership communication appears on the same page as other ISP partner communications (brands other than SKY/Amazon), THE Compliance_Analyzer SHALL report a "FAIL" status for the "kv_integrity" Compliance_Rule indicating an exclusivity violation.
6. WHEN all KV integrity rules (criteria 1 through 5) are satisfied, THE Compliance_Analyzer SHALL report a "PASS" status for the "kv_integrity" Compliance_Rule.
7. IF no KV element is detected on the ISP_Website screenshot, THEN THE Compliance_Analyzer SHALL skip the "kv_integrity" Compliance_Rule evaluation and record it as "not_applicable" in the Compliance_Report.

### Requirement 6: Construção de Prompt com Regras e Imagens de Referência

**User Story:** As a developer, I want the analyzer to build a multimodal prompt that includes all compliance rules AND reference images for comparison, so that the AI has complete context for accurate validation.

#### Acceptance Criteria

1. THE Compliance_Prompt_Builder SHALL include in the prompt text sent to Bedrock exactly 5 compliance rule sections: facilitator_role, logo_application, content_separation, naming_pricing, and kv_integrity, each with its respective validation criteria as text.
2. THE Compliance_Prompt_Builder SHALL include the ISP_Website screenshot being analyzed as the primary image in the multimodal request to Bedrock, labeled with its role as the image under analysis.
3. THE Compliance_Prompt_Builder SHALL include the Reference_Image files from `watchdog_rules/SKY_Amazon_Imagens/` as additional images in the multimodal request to Bedrock, each labeled with a textual description of its purpose for comparison.
4. WHEN the Reference_Image `Artes_aprovadas_referencia.PNG` is available, THE Compliance_Prompt_Builder SHALL include the image with context explaining it shows approved art examples for comparison.
5. WHEN the Reference_Image `Logo_errado_logo_correto.PNG` is available, THE Compliance_Prompt_Builder SHALL include the image with context explaining it shows wrong logo usage versus correct logo usage.
6. WHEN the Reference_Image `logo_sky_plus_amazon.PNG` is available, THE Compliance_Prompt_Builder SHALL include the image with context explaining it shows the official SKY+ with Amazon Prime logo.
7. IF a Reference_Image file is missing or unreadable, THEN THE Compliance_Prompt_Builder SHALL log a warning indicating the filename and reason of failure, and proceed with the remaining available images without failing the analysis.
8. IF all Reference_Image files are missing or unreadable, THEN THE Compliance_Prompt_Builder SHALL log a warning for each file and proceed with the analysis using only the ISP_Website screenshot and the compliance rules text.

### Requirement 7: Estruturação de Resultados por Regra de Compliance

**User Story:** As a brand compliance analyst, I want results structured per compliance rule (pass/fail), so that I can quickly identify which specific rules each ISP is violating.

#### Acceptance Criteria

1. THE Compliance_Analyzer SHALL return a Compliance_Report containing one result entry per Compliance_Rule evaluated, covering all configured rules (facilitator_role, logo_application, logo_effects, content_separation, naming_pricing, kv_integrity).
2. WHEN analysis is complete, THE Compliance_Report SHALL include for each rule: rule identifier (string matching the configured rule name), status (exactly "PASS" or "FAIL"), confidence score (integer 0-100), and a textual description of findings with a maximum length of 1024 characters.
3. WHEN at least one Compliance_Rule has status "FAIL", THE Compliance_Report SHALL set the overall compliance status to "non_compliant".
4. WHEN all Compliance_Rules have status "PASS", THE Compliance_Report SHALL set the overall compliance status to "compliant".
5. THE Compliance_Analyzer SHALL parse the Bedrock response into the Compliance_Report structure, and FOR ALL valid Compliance_Report objects, serializing then deserializing SHALL produce a field-by-field identical object (same rule identifiers, statuses, confidence scores, and descriptions).
6. IF the Bedrock response cannot be parsed into a valid Compliance_Report structure (malformed, missing required fields, or invalid values), THEN THE Compliance_Analyzer SHALL log the parsing error with the raw response size and timestamp, and return an error indication to the caller without producing a partial Compliance_Report.
7. IF the Bedrock response contains results for fewer rules than the configured set of Compliance_Rules, THEN THE Compliance_Analyzer SHALL treat the response as invalid, log which rules are missing, and apply the same error handling as an unparseable response.

### Requirement 8: Envio de Email com Relatório de Compliance

**User Story:** As a brand compliance analyst, I want to receive an email report for every monitored ISP regardless of compliance status, so that I have a complete audit trail of all validations.

#### Acceptance Criteria

1. WHEN a compliance analysis cycle is completed for an ISP_Website, THE Email_Notifier SHALL send exactly one consolidated report email per ISP_Website per cycle to all configured recipients, regardless of whether the overall status is "compliant" or "non_compliant".
2. WHEN the overall status is "non_compliant", THE Email_Notifier SHALL include in the email: the ISP URL, the analysis timestamp in ISO 8601 format, the list of failed Compliance_Rules with their descriptions and confidence scores, and the overall compliance status.
3. WHEN the overall status is "compliant", THE Email_Notifier SHALL include in the email: the ISP URL, the analysis timestamp in ISO 8601 format, confirmation that all rules passed, and the overall compliance status.
4. THE Email_Notifier SHALL format the email with a structured summary section listing each Compliance_Rule evaluated, its pass/fail status, and its confidence score (0-100).
5. IF the Email_Notifier fails to send a report email, THEN THE Email_Notifier SHALL retry up to 3 times with a 30-second interval between attempts, and log the failure with the recipient address and ISP URL if all retries are exhausted.
6. IF all retry attempts are exhausted for a recipient, THEN THE Email_Notifier SHALL continue sending to the remaining configured recipients without blocking the compliance cycle.

### Requirement 9: Modelo de Dados para Compliance Results

**User Story:** As a developer, I want to persist compliance results in a structured format per rule, so that historical data can be queried and compliance trends tracked.

#### Acceptance Criteria

1. THE Compliance_Analyzer SHALL persist each Compliance_Report to the database with: target site identifier, monitoring cycle identifier, timestamp (UTC timezone-aware), overall compliance status (one of: "compliant", "non_compliant", "partial", "error"), and the individual rule results.
2. WHEN a Compliance_Report is saved, THE Compliance_Analyzer SHALL store each rule result with: rule identifier (max 128 characters), status ("pass" or "fail"), confidence score (integer 0 to 100), and description (max 1024 characters).
3. WHEN a rule result has status "fail", THE Compliance_Analyzer SHALL create a corresponding DetectionResult record using the rule identifier as match_type, the confidence score as confidence, zeroed bounding box coordinates (0.0 for all four fields), and the rule description as description, so that existing alert suppression and retention cleanup logic applies to compliance violations.
4. IF persistence of a Compliance_Report fails after 3 retry attempts with exponential backoff, THEN THE Compliance_Analyzer SHALL raise an exception and log an error message indicating the target site identifier and the monitoring cycle identifier that failed.
5. THE Compliance_Analyzer SHALL calculate an expires_at value for each persisted compliance record using the configured detection_retention_days, consistent with the existing DetectionResult expiration mechanism.

### Requirement 10: Suporte a Múltiplas Imagens no Request Bedrock

**User Story:** As a developer, I want the Bedrock client to support sending multiple images in a single request, so that reference images can be included alongside the screenshot for comparison.

#### Acceptance Criteria

1. THE Bedrock_Client SHALL support sending up to 5 images (1 screenshot + up to 4 reference images) in a single multimodal request to the Claude model via the Bedrock Messages API.
2. WHEN multiple images are included in the request, THE Bedrock_Client SHALL label each image with its role using a text block preceding each image block: "screenshot_under_analysis", "approved_art_reference", "correct_logo_reference", "wrong_logo_example".
3. THE Bedrock_Client SHALL maintain the existing retry logic (3 attempts, exponential backoff 2s, 4s, 8s) and timeout (60 seconds) for multi-image requests.
4. IF the total payload size exceeds 20 MB (Bedrock request limit), THEN THE Bedrock_Client SHALL log an error with the actual payload size and proceed with the screenshot only, omitting reference images.
5. IF individual reference images exceed 5 MB each, THEN THE Bedrock_Client SHALL skip that specific image, log a warning with the filename and size, and include the remaining images that are within limits.
