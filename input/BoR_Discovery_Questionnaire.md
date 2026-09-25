# Discovery Questionnaire: Bank of Riyadh Employee Self Service AI Agent

Prepared by Spark Technologies

## Purpose

This questionnaire supports the Discovery phase of the project plan. Responses from Bank of Riyadh's Enterprise Applications, Oracle, Identity, Security, and HR teams will determine the integration architecture and pilot scope.

## Section 1: Oracle EBS R12 Environment

1. Which EBS R12 modules are relevant to this project, such as Self Service HR, Oracle Time and Labor, Payroll, and iRecruitment.
2. Is there an existing middleware layer in front of EBS, such as Oracle Integration Cloud or SOA Suite, and if so, which functions are already exposed as REST or SOAP endpoints.
3. Are there any customizations to standard EBS workflows that would affect leave, payslip, or profile update processes.
4. Is a non production or sandbox EBS environment available for integration testing.
5. Who is the technical owner responsible for EBS integration decisions.

## Section 2: Identity and Access

1. What identity provider and protocol is used for employee authentication, such as Azure AD with SAML or OAuth.
2. How are employee, manager, HR, and administrator roles currently defined and enforced, and can this be queried through an API or directory service.
3. Are session timeout limits or step up authentication required for sensitive transactions.
4. Who is the technical owner responsible for identity and access decisions.

## Section 3: Data and Privacy

1. What is the Bank's data classification policy, and which employee data falls under restricted or sensitive categories that must be masked or excluded from AI prompts.
2. What are the retention requirements for conversation logs and audit records, and are there requirements for data residency or approved hosting locations.
3. Is production data permitted in any phase of this project, or must all testing use synthetic or masked data.
4. Who is the owner responsible for data privacy and classification decisions.

## Section 4: Security and Compliance

1. What is the required process for security review and approval of a new system such as this.
2. What audit and logging standards must be followed for user actions and system decisions, and are there separation of duties requirements affecting agent actions.
3. What is the incident response process for issues involving employee data or unauthorized actions.
4. Who is the owner responsible for security and compliance sign off.

## Section 5: HR Process and Content

1. What are the highest volume employee requests today, such as leave balance checks or HR policy questions.
2. Where does official HR policy content currently reside, is it available in both Arabic and English, and who owns updates to it.
3. Are there existing FAQs, knowledge base articles, or policy documents that can be used as source content.
4. Who is the owner responsible for HR content accuracy and updates.

## Section 6: Language Requirements

1. Is formal Arabic sufficient, or should the system accommodate common workplace Saudi Arabic phrasing.
2. Are there Bank approved Arabic and English terms for policies, services, and statuses that must be used consistently.
3. Who is the owner responsible for reviewing language quality and terminology.

## Section 7: Operations and Support

1. What are the required service hours and availability targets, and what is the expected concurrent user load during peak periods.
2. What support model is expected once the pilot goes live, including escalation path and response time targets.
3. Who is the owner responsible for ongoing operations and support once the pilot is live.

## Section 8: Pilot Scope and Success Criteria

1. Which three to five employee journeys should be prioritized for the pilot, and what cohort size is proposed for initial testing.
2. What does the Bank consider a successful pilot outcome, and are there existing baseline metrics such as self service completion rate or support ticket volume.
3. Who has final approval authority to move from pilot to expanded rollout.
