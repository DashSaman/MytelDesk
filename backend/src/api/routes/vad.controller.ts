import { Controller } from '@nestjs/common';
import { vadHealth } from '../../services/vad-health';
import { Auth, Get } from '../routing';

@Controller()
export class VadController {
  @Get('/api/vad/status')
  @Auth('admin')
  status() {
    return vadHealth.check();
  }
}
