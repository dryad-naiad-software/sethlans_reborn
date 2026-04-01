import { Component } from '@angular/core';
import { MatIconModule } from '@angular/material/icon';

@Component({
  selector: 'app-auth-banner',
  standalone: true,
  imports: [MatIconModule],
  template: `
    <div class="auth-banner">
      <mat-icon>warning</mat-icon>
      <span>
        Authentication is not enabled. This instance should only be used on a
        trusted network.
      </span>
    </div>
  `,
  styles: [`
    .auth-banner {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 8px 16px;
      background-color: #fff3e0;
      color: #e65100;
      font-size: 14px;
      border-bottom: 1px solid #ffe0b2;
    }
  `],
})
export class AuthBannerComponent {}
