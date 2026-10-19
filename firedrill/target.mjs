import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { resolve, join } from 'node:path';
import { ApplicationError, commandMain, invokeCLI, privateDirectory } from './adapter-runtime.mjs';
const root = fileURLToPath(new URL('../', import.meta.url));
const cases = new Set(JSON.parse(readFileSync(new URL('../data/index.json', import.meta.url))).cases.map(item => item.id));
export async function runApplication(input, parameters, signal, invocation) {
  if (Object.keys(input).some(key => !['caseId', 'action', 'requestId', 'allowProposal'].includes(key)) || !cases.has(input.caseId) || !['recommend', 'inspect'].includes(input.action) ||
      typeof input.requestId !== 'string' || !/^[A-Za-z0-9_-]{1,80}$/.test(input.requestId) || typeof input.allowProposal !== 'boolean') throw new Error('Supply a reviewed case, exact request, action and explicit independent proposal grant.');
  const directory = await privateDirectory(root, invocation), state = join(directory, 'workspace.json');
  const cli = ['uv', 'run', '--locked', '--no-sync', 'purchase-request'];
  try {
    await invokeCLI([...cli, 'init', '--state', state, '--case', input.caseId], { cwd: root, signal });
    const argv = [...cli, input.action === 'inspect' ? 'context' : 'recommend', '--state', state, '--request', input.requestId];
    if (input.action === 'recommend') {
      argv.push('--live', '--max-provider-requests', '3', '--output-root', join(directory, 'receipts'));
      if (input.allowProposal) argv.push('--allow-writes');
      if (parameters.model) argv.push('--model', parameters.model);
    }
    return { executionKind: input.action === 'inspect' ? 'application_state_inspection' : 'native_crew_recommendation',
      application: await invokeCLI(argv, { cwd: root, signal }), localState: { directory, workspaceFile: state } };
  } catch (error) { if (error instanceof ApplicationError) error.evidence.runDirectory = directory; throw error; }
}
if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) await commandMain(runApplication);
